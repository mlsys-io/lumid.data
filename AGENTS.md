# AGENTS.md — lumid.data

Guidance for agentic tools (Claude Code, Codex, Cursor, Aider) working in
this repo. `CLAUDE.md` redirects here.

## Project Overview

`lumid.data` is a **data management service** with two surfaces under
one unified URL:

1. **Traditional CRUD endpoints** — REST over a Postgres database
   (`/db`), an S3-compatible object store (`/storage`), plus a
   passthrough SQL gateway (`/sql`). Power users + SDKs hit these
   directly.
2. **LLM-driven data agent** — `/agent/v1` runs a tool-use loop over
   the same CRUD endpoints; `/mcp` exposes the same tools to any MCP
   client. Same auth, same audit log.

The agent is **chat-with-data** (Snowflake Cortex / Databricks Genie
lane): an LLM orchestrates the CRUD calls. There is no privileged
backdoor — the agent uses the same URLs a direct client would.

## Architecture

```
   ┌── lumid apps / direct clients / MCP hosts ──┐
   │  curl, psycopg, awscli, Claude Desktop      │
   └──────────────┬──────────────────────────────┘
                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  lumid.data  FastAPI (port 9100, bearer-token gated)    │
   │   /db/v1/*       → reverse-proxy to PostgREST sidecar   │
   │   /storage/v1/*  → boto3 over MinIO (sign / put / get)  │
   │   /sql/v1        → psycopg passthrough (role-scoped)    │
   │   /agent/v1      → LLM tool-use loop, SSE streaming     │
   │   /mcp           → MCP server (CRUD endpoints as tools) │
   │   /v1/admin/*    → audit_log + agent_runs read views    │
   │   /healthz, /docs                                       │
   └────┬─────────────┬─────────────┬──────────────┬─────────┘
        ▼             ▼             ▼              ▼
   PostgREST     Postgres       MinIO        LLM provider
   (sidecar)    public + meta   buckets      (Anthropic |
                                              OpenAI |
                                              OpenAI-compat)

   audit:  every CRUD + agent run -> lumid_data_meta.audit_log
           optional NATS fan-out on lumid.data.audit.{op}
```

## Components

| Path | Purpose |
|------|---------|
| `src/lumid_data/server/main.py` | FastAPI app + lifespan |
| `src/lumid_data/server/routers/` | health, db_proxy, storage, sql, streams, agent, admin, mcp_mount |
| `src/lumid_data/server/services/` | postgrest_jwt, audit, s3 |
| `src/lumid_data/server/auth/security.py` | bearer chain + scopes |
| `src/lumid_data/streams/` | webhook + websocket + kafka adapters, sinks, supervised runner |
| `src/lumid_data/agent/` | provider-agnostic tool-use runner + tool catalog |
| `src/lumid_data/agent/providers/` | anthropic / openai / openai_compat |
| `src/lumid_data/mcp_server/` | builds MCP server over the FastAPI app |
| `src/lumid_data/db/` | SQLAlchemy models for `lumid_data_meta` |
| `src/lumid_data/sdk/` | HTTP SDK for direct or app-side use |
| `src/lumid_data/cli/` | `lumid-data {stack,sql,storage,agent,admin}` |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | one-click `docker compose up -d` (root); `scripts/init-postgres.sql` is mounted by the postgres service |

## OSS stack

| Slot | Choice |
|------|--------|
| Database | TimescaleDB on PostgreSQL 16 (hypertables opt-in per stream) |
| DB REST gateway | PostgREST OSS (sidecar) |
| Object store | MinIO (S3-compatible) |
| Streaming bus | Redpanda (Kafka API; only needed for kafka-transport streams) |
| Audit fan-out | NATS (optional) |
| LLM | Anthropic / OpenAI / OpenAI-compatible (Ollama, vLLM, …) |
| Agent protocol | MCP (Model Context Protocol) for tool exposure |
| Identity | bearer-token chain; OAuth/OIDC introspection via plugin |

## Setup

```bash
pip install uv
uv sync --extra dev                       # all deps including dev tooling
uv run pre-commit run --all-files         # before any commit
uv run pytest tests/                      # unit tests
uv run lumid-data stack up                # full local stack
```

## Hook Plugin Extension Points

Plugins are Python modules with a top-level `install()` (sync or
`@asynccontextmanager async def`). Loaded from `LUMID_DATA_PLUGINS` env
var (CSV of importable module names) at FastAPI lifespan startup.

- `IdentityProvider` — resolve a bearer token to a principal
  (`server/auth/security.py`).

External OAuth/OIDC providers are integrated via `IdentityProvider`
plugins loaded at runtime through `LUMID_DATA_PLUGINS=<module.name>`.
With no plugin registered, the bearer chain is a no-op and any request
is treated as the default admin (OSS local-dev shape).

## API Reference (`http://localhost:9100`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | health check |
| ANY | `/db/v1/{table}` (PostgREST shape) | CRUD with `id=eq.1` filters |
| GET | `/storage/v1/object/{bucket}/{path}` | download |
| PUT | `/storage/v1/object/{bucket}/{path}` | upload |
| POST | `/storage/v1/upload/sign/{bucket}/{path}` | presigned PUT |
| GET | `/storage/v1/list/{bucket}` | list objects |
| POST | `/sql/v1` | run a single SQL statement |
| POST | `/v1/streams` | register a stream descriptor |
| GET | `/v1/streams` / `/v1/streams/{id}` | list / get |
| POST | `/v1/streams/{id}/state` | activate / pause / stop |
| GET | `/v1/streams/{id}/status` | last run + lag |
| POST | `/v1/ingest/{id}` | webhook push |
| WS | `/v1/ingest/ws/{id}` | websocket push |
| GET | `/v1/streams/{id}/dlq` / `POST .../dlq/{dlq_id}/replay` | DLQ |
| POST | `/agent/v1` | streamed agent tool-use (SSE) |
| GET | `/mcp` | MCP server (streamable HTTP) |
| GET | `/v1/admin/audit` / `/v1/admin/runs` | audit + agent-run views |

## Object Identifiers

- `aud-` audit_log rows
- `run-` agent_runs
- `str-` stream_sources rows
- `srn-` stream_runs rows
- `dlq-` stream_dlq rows

ID factories live in `src/lumid_data/utils/ids.py`.

## SDK Usage (`lumid_data.sdk`)

```python
from lumid_data.sdk import Client

client = Client(base_url="http://localhost:9100", token=os.environ["LUMID_TOKEN"])

# /db
rows = client.db_select("users", id="eq.1")

# /storage
client.storage_put("photos", "cat.png", open("cat.png", "rb").read(), mime="image/png")

# /sql
result = client.sql("SELECT count(*) FROM users")

# /agent (streams SSE)
for event, payload in client.agent_run("how many users do we have?"):
    print(event, payload)
```

## CLI Commands

```
lumid-data stack {up, down, restart, ps, logs, build, pull}
lumid-data sql query "SELECT 1"
lumid-data storage {get, put, ls, rm}
lumid-data agent run "..."
lumid-data admin {audit, runs, run}
lumid-data healthz
```

## Environment Variables

Canonical declared set in `src/lumid_data/server/config.py`, mirrored
to `.env.example` at the repo root.

| Variable | Default | Description |
|----------|---------|-------------|
| `LUMID_DATA_HTTP_PORT` | `9100` | Public HTTP port |
| `LUMID_DATA_BASE_URL` | – | URL the agent calls back into for tool dispatch |
| `DATABASE_URL` | – | Postgres connection string |
| `S3_ENDPOINT` | – | MinIO / S3 endpoint |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | – | MinIO credentials |
| `S3_DEFAULT_BUCKET` | `lumid-data` | default bucket |
| `POSTGREST_URL` | `http://postgrest:3000` | sidecar URL for `/db` proxy |
| `POSTGREST_JWT_SECRET` | – | shared JWT secret with PostgREST |
| `POSTGREST_JWT_TTL_SEC` | `60` | JWT lifetime |
| `NATS_URL` | – | optional audit fan-out |
| `LUMID_DATA_LLM_PROVIDER` | `anthropic` | `anthropic | openai | openai_compat` |
| `LUMID_DATA_LLM_MODEL` | `claude-sonnet-4-6` | model name |
| `LUMID_DATA_LLM_API_KEY` | – | provider API key |
| `LUMID_DATA_LLM_BASE_URL` | – | base URL for `openai_compat` |
| `LUMID_DATA_AGENT_MAX_STEPS` | `20` | tool-use loop budget |
| `KAFKA_BOOTSTRAP` | – | Redpanda/Kafka bootstrap; required only for kafka-transport streams |
| `LUMID_DATA_PLUGINS` | – | CSV of plugin module names |
| `LOG_LEVEL` | `INFO` | log level |

## Code Style

- Python 3.12+. Type hints throughout.
- Top-level imports only; inline imports only to break circular imports.
- Pydantic v2 models for API + DB schemas.
- `X | Y` over `typing.Union[X, Y]`; `X | None` over `typing.Optional[X]`.
- `typing.Any` over `object` in annotations.
- No `print()` — use the project logger.
- No `# type: ignore` without a specific error code.
- Default to no comments. Comment only when *why* is non-obvious.
- No back-compat shims when updating code; replace outright.

## Repo Boundaries

`lumid.data` is OSS-shaped and stands on its own. Upstream consumers
and proprietary plugins must not appear in this repo — code, docs,
comments, env-var examples, identifiers, or commit messages.

- **No upstream-consumer names.** Do not reference any project that
  *consumes* lumid.data (e.g. compute engines, workflow optimizers).
  Style decisions stand on their own (`Enforced: B113, B202, …`), not
  framed as "matches X" or "X parity."
- **No proprietary plugins.** Identity providers, lineage sinks, and
  similar are loaded at runtime via `LUMID_DATA_PLUGINS=<module>` from
  out-of-tree packages. The plugin module name is a deploy-time
  config; it never appears in this repo's source.
- **Neutral plugin language.** Describe extension points by their
  Protocol (e.g. `IdentityProvider` resolves a bearer token to a
  `PrincipalContext`), not by a specific implementation.

A history rewrite was performed once to enforce this; future code
must keep history clean by following these rules at write time.

## Security Rules (bandit-enforced)

Enforced: B113, B202, B310, B324, B506, B607, B614, B701, B108.
Skipped rules and rationale live in `[tool.bandit]`. No bare `# nosec`.

## Commit Conventions

- Single-line subject in imperative mood.
- Conventional prefix: `feat`, `fix`, `refactor`, `chore`, `docs`,
  `style`, `test`. Scope optional: `feat(server): ...`.
- DCO sign-off (`--signoff`) required for code from coding agents.
- One logical change per commit.

## Pull Requests

PR title format: `type(scope): description`. Allowed types:
`feat, fix, refactor, chore, test, perf, build, ci, docs`. Prefix
`[BREAKING]` for breaking changes.

Never open / comment on / reply to a GitHub PR without explicit user
approval. Pushing commits to a feature branch is fine.
