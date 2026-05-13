# AGENTS.md — lumid.data

Guidance for agentic tools (Claude Code, Codex, Cursor, Aider) working in
this repo. `CLAUDE.md` redirects here.

## Project Overview

`lumid.data` is a **data management service** with two surfaces under
one unified URL:

1. **Traditional CRUD endpoints** — a passthrough SQL gateway (`/sql`)
   over Postgres, plus an S3-compatible object store (`/storage`).
   Power users + SDKs hit these directly.
2. **LLM-driven data agent** — `/agent/v1` runs a tool-use loop over
   the same CRUD endpoints plus deterministic schema-card and
   replay/materialization tools; `/mcp` exposes the CRUD tools to any
   MCP client. Same auth, same audit log.

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
   │  lumid.data  FastAPI (port 9100)                        │
   │   /sql/v1        → psycopg passthrough (admin role)     │
   │   /storage/v1/*  → boto3 over MinIO (sign / put / get)  │
   │   /agent/v1      → LLM tool-use loop, SSE streaming     │
   │   /mcp           → MCP server (CRUD endpoints as tools) │
   │   /v1/admin/*    → audit_log + agent_runs read views    │
   │   /healthz, /docs                                       │
   └────────┬───────────────────┬──────────────────┬─────────┘
            ▼                   ▼                  ▼
        Postgres              MinIO          LLM provider
        public + meta         buckets        (Anthropic |
                                              OpenAI |
                                              OpenAI-compat)

   audit:  every CRUD + agent run -> lumid_data_meta.audit_log
           optional NATS fan-out on lumid.data.audit.{op}
```

## Components

| Path | Purpose |
|------|---------|
| `src/lumid_data/server/main.py` | FastAPI app + lifespan |
| `src/lumid_data/server/routers/` | health, storage, sql, streams, agent, admin, mcp_mount |
| `src/lumid_data/server/skills.py` | named agent workflow instructions such as `data_retrieval` |
| `src/lumid_data/server/services/retrieval_tools.py` | data-agent tools for schema cards and replay/materialization |
| `src/lumid_data/server/services/` | audit, s3 |
| `src/lumid_data/server/auth/security.py` | bearer-token shim; delegates to the IDENTITY_PROVIDERS chain (no-op when empty) |
| `src/lumid_data/server/hooks/` | plugin extension protocols + registries |
| `src/lumid_data/streams/` | webhook + websocket + kafka adapters, sinks, supervised runner |
| `src/lumid_data/agent/` | provider-agnostic tool-use runner + tool catalog |
| `src/lumid_data/agent/providers/` | anthropic / openai / openai_compat |
| `src/lumid_data/mcp_server/` | builds MCP server over the FastAPI app |
| `src/lumid_data/db/` | SQLAlchemy models for `lumid_data_meta` |
| `src/lumid_data/sdk/` | HTTP SDK for direct or app-side use |
| `src/lumid_data/cli/` | `lumid-data {stack,sql,storage,agent,admin}` |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | one-click `docker compose up -d` (root); `scripts/init-postgres.sql` is mounted by the postgres service |

## Component stack

| Slot | Choice |
|------|--------|
| Database | TimescaleDB on PostgreSQL 16 (hypertables opt-in per stream) |
| Object store | MinIO (S3-compatible) |
| Streaming bus | Redpanda (Kafka API; only needed for kafka-transport streams) |
| Audit fan-out | NATS (optional) |
| LLM | Anthropic / OpenAI / OpenAI-compatible (Ollama, vLLM, …) |
| Agent protocol | MCP (Model Context Protocol) for tool exposure |

## Setup

```bash
pip install uv
uv sync --all-packages --group dev        # all deps including dev tooling
uv run pre-commit run --all-files         # before any commit
uv run pytest tests/                      # unit tests
uv run lumid-data stack up                # full local stack
```

## Hook Plugin Extension Points

Plugins are Python modules with a top-level `install()` (sync or
`@asynccontextmanager async def`). Loaded from `LUMID_DATA_PLUGINS` env
var (CSV of importable module names) at FastAPI lifespan startup.

- `IdentityProvider` — resolve a bearer token to a `PrincipalContext`
  (`server/hooks/identity.py`).

With no plugin registered, `authenticate_api_key` returns
`default_principal()` — auth is off and every caller is admin
(local-dev shape). Routers use `default_principal()` directly to
short-circuit auth in the unconfigured case.

## API Reference (`http://localhost:9100`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | health check |
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

client = Client(base_url="http://localhost:9100", token=os.environ.get("LUMID_TOKEN"))

# /sql
result = client.sql("SELECT count(*) FROM users")

# /storage
client.storage_put("photos", "cat.png", open("cat.png", "rb").read(), mime="image/png")

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
| `DB_ADMIN_ROLE` | `app_admin` | Postgres role `/sql/v1` switches to via `SET LOCAL ROLE` |
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

### Python

- Python 3.12+. Type hints throughout.
- Top-level imports only; inline imports only to break a genuine
  circular import or to gate an optional heavy dependency behind
  `try/except`.
- Never `importlib`. Use `from xxx import xxx` or `import xxx`.
- Pydantic v2 models for API + DB schemas. Avoid `.get()` / `getattr`
  when a key is known to exist.
- Prefer `X | Y` over `typing.Union[X, Y]`; `X | None` over
  `typing.Optional[X]`.
- Prefer `typing.Any` over `object` in annotations. Only use `object`
  when `Any` is semantically wrong (e.g. framework override signatures).
- Don't write `from __future__ import annotations` unless strictly
  necessary. Use `typing.Self` or quoted forward references instead.
- Avoid `hasattr` / `getattr` that bypasses type checking. Use
  `isinstance` guards. Acceptable `getattr` uses: dynamic dispatch,
  providing a default (`getattr(obj, attr, default)`), or reaching
  into untyped third-party libraries.
- `# type: ignore[<error-code>]` only after exhausting alternatives.
  Never a bare `# type: ignore`. Missing-dep type errors → add the dep
  to `pyproject.toml`, then `uv lock`.
- No `print()` — use the project logger.
- In `except`, only raise. Don't swallow library errors into booleans
  or `None`-returns. The narrow exception is a documented not-found
  probe where the library has no list-based alternative — translate
  only the documented missing-resource case and re-raise everything
  else.
- No back-compat shims when updating code; replace outright. This
  includes re-exports, aliases, no-op stubs, and "ported from"
  breadcrumb comments.

### Comments and docstrings

- Default to **no comments**. Comment only when *why* is non-obvious;
  names self-document.
- Don't reference the current task / fix / caller in comments ("used
  by X", "added for Y", "handles issue #123") — those rot as the
  codebase evolves.
- Docstrings describe what the code *does*, not what it *replaced* or
  what it resembles. No "in-process replacement for X", no "previously
  did Y", no "mirrors X / parity with X". Read the docstring as if
  seeing the code for the first time.

## Repo Boundaries

`lumid.data` stands on its own. Upstream consumers must not appear in
this repo — code, docs, comments, env-var examples, identifiers, or
commit messages.

- **No upstream-consumer names.** Do not reference any project that
  *consumes* lumid.data (e.g. compute engines, workflow optimizers).
  Style decisions stand on their own (`Enforced: B113, B202, …`), not
  framed as "matches X" or "X parity."

A history rewrite was performed once to enforce this; future code
must keep history clean by following these rules at write time.

## Security Rules (bandit-enforced)

CI runs `bandit` with no severity / confidence threshold. Every
finding must have a source-level fix, a documented skip in
`[tool.bandit]` in `pyproject.toml`, or a per-line `# nosec BXXX` with
a one-line written rationale at the call site. A bare `# nosec` (no
rule code, no reason) is disallowed.

When writing new code, follow these rules:

- **B113** — every `requests.get/post/...` call passes `timeout=`. No
  implicit defaults; hung connections are a DoS.
- **B202** — `tarfile.extractall(..., filter="data")` (Python 3.12+).
  For zipfile, iterate `infolist()`, validate each member resolves
  under the destination, extract per-member. Never `zipfile.extractall`
  on untrusted archives.
- **B310** — don't use `urllib.request.urlopen`. Use `requests` and
  validate the URL scheme (`http`/`https` only) before fetching.
- **B324** — `hashlib.md5(..., usedforsecurity=False)` for cache-key /
  fingerprint use. Never MD5 across a security boundary.
- **B506** — `yaml.safe_load`, never `yaml.load(..., Loader=FullLoader)`.
- **B603** — every `subprocess.run/call/Popen/...` needs a per-line
  `# nosec B603` with a one-line rationale (e.g. `argv list, no
  shell=True, absolute path via shutil.which()`). The B404 import-level
  rule is project-skipped because B602/B607 catch the actually-dangerous
  patterns; B603 is enforced per-site so every shellout is visible at
  the call line.
- **B607** — prefer the vendored SDK over shelling out via `nvidia-smi`
  / `docker` / `git`. If shelling out is unavoidable, the absolute path
  must be provided.
- **B614** — `torch.load(..., weights_only=True)`. Pickle deserialization
  is RCE waiting to happen.
- **B701** — `Environment(autoescape=select_autoescape())`. The default
  `False` is unsafe even for non-HTML templates.
- **B108** — use `tempfile.gettempdir()` or `tempfile.NamedTemporaryFile`.
  The literal `"/tmp"` in Python source is forbidden; for an
  in-container sentinel, build it from `PurePosixPath` segments.

Skipped rules and the rationale for each live in `[tool.bandit]`.
When a documented skip stops being true (e.g. a sandbox stops being a
sandbox), remove the skip and fix the call sites — don't widen the
skip list silently.

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
