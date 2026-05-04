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
   │  lumid.data  FastAPI (port 9100, OIDC gated)     │
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
| `src/lumid_data/server/routers/` | health, db_proxy, storage, sql, agent, admin, mcp_mount |
| `src/lumid_data/server/services/` | postgrest_jwt, audit, s3 |
| `src/lumid_data/server/auth/security.py` | bearer chain + scopes |
| `src/lumid_data/auth/introspect.py` | OIDC plugin |
| `src/lumid_data/agent/` | provider-agnostic tool-use runner + tool catalog |
| `src/lumid_data/agent/providers/` | anthropic / openai / openai_compat |
| `src/lumid_data/mcp_server/` | builds MCP server over the FastAPI app |
| `src/lumid_data/db/` | SQLAlchemy models for `lumid_data_meta` |
| `src/lumid_data/client/` | HTTP SDK for direct or app-side use |
| `src/lumid_data/cli/` | `lumid-data {stack,sql,storage,agent,admin}` |
| `deploy/` | one-click `docker compose up -d` |

## OSS stack

| Slot | Choice |
|------|--------|
| Database | PostgreSQL 16 |
| DB REST gateway | PostgREST OSS (sidecar) |
| Object store | MinIO (S3-compatible) |
| Audit fan-out | NATS (optional) |
| LLM | Anthropic / OpenAI / OpenAI-compatible (Ollama, vLLM, …) |
| Agent protocol | MCP (Model Context Protocol) for tool exposure |
| Identity | OIDC (`/introspect`) |

## Setup

```bash
pip install uv
uv sync --extra dev                       # all deps including dev tooling
uv run pre-commit run --all-files         # before any commit
uv run pytest tests/                      # unit tests
uv run lumid-data stack up                # full local stack
```

## Hook Plugin Extension Points

Same contract as identity-provider. Plugins are Python modules with a
top-level `install()` (sync or `@asynccontextmanager async def`).
Loaded from `LUMID_DATA_PLUGINS` env var (CSV of importable module
names) at FastAPI lifespan startup.

- `IdentityProvider` — resolve a bearer token to a principal
  (`server/auth/security.py`).

(internal note): `IdentityProvider` in
`auth/introspect.py` mirrors the IdentityProvider Protocol.

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
| POST | `/agent/v1` | streamed agent tool-use (SSE) |
| GET | `/mcp` | MCP server (streamable HTTP) |
| GET | `/v1/admin/audit` / `/v1/admin/runs` | audit + agent-run views |

## Object Identifiers

- `aud-` audit_log rows
- `run-` agent_runs

ID factories live in `src/lumid_data/utils/ids.py`.

## SDK Usage (`lumid_data.client`)

```python
from lumid_data.client import Client

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
to `deploy/.env.example`.

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
| `OIDC_INTROSPECT_URL` | – | lumid introspect endpoint |
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

(internal note): never post to GitHub
(open PR / comment / reply) without explicit user approval.
