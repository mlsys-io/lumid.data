# AGENTS.md — lumid.data

Guidance for agentic tools (Claude Code, Codex, Cursor, Aider) working in
this repo. `CLAUDE.md` redirects here.

## Project Overview

`lumid.data` is the data plane that connects **lumid** (auto-research
front end), **FlowMesh** (execution engine), and **Lumilake** (workflow
optimizer). It owns the **data agent** — a bounded data-engineering
agent in the lineage of Sun et al., *Autonomous Data Agents*
(arXiv 2509.18710), and Dataforge (arXiv 2511.06185). Four-stage loop:

```
Perception (modality detect) → Planning (policy table) →
Grounding (schema reconcile + QC) → Execution (sinks) → Reflection
```

The agent is *not* a chat-with-data agent. Its job: given a payload + a
registered `SourceDescriptor`, produce an `IngestPlan` and execute it
deterministically against governed sinks (Delta, Unity Catalog volumes,
RisingWave, Milvus, TimescaleDB).

## Architecture

```
                lumid apps (CLI + Python skills)
                  │  imports lumid_data.client
                  ▼
   ┌─────────────────────────────────────────────┐
   │  lumid.data  FastAPI (port 9100)             │
   │   inbound:  REST + WS + pull connectors      │
   │   agent:    modality / schema / QC / route   │
   │   sinks:    delta / risingwave / objects /   │
   │             milvus / timescale / dlq         │
   │   catalog:  Unity Catalog (table-of-record)  │
   │             FlowMesh governance (lineage)    │
   │             NATS DatasetReady event          │
   └─────────────────────────────────────────────┘
        │              │            │
        ▼              ▼            ▼
   MinIO+Delta    Redpanda+RW    Milvus / Timescale
        │
        ▼
   Unity Catalog ── Hive Metastore (mirror)
        │
        ▼
   Lumilake (reads governance, plans via OaaS-Runmesh, submits to FlowMesh)
        │
        ▼
   FlowMesh (data_retrieval+delta, data_profiling+delta, ...)
```

## Components

| Path | Purpose |
|------|---------|
| `src/lumid_data/server/` | FastAPI app, routers, lifespan |
| `src/lumid_data/agent/` | modality, schema, quality, decisions, router |
| `src/lumid_data/sources/` | webhook, rest_poll, s3_watch, file_drop, cdc_pg |
| `src/lumid_data/sinks/` | delta, risingwave, objects, milvus, timescale, dlq |
| `src/lumid_data/catalog/` | unity, hms, flowmesh_governance, nats_publisher |
| `src/lumid_data/schemas/` | pydantic v2: descriptors, lineage |
| `src/lumid_data/auth/` | lumid_introspect (mirrors Lumilake's plugin) |
| `src/lumid_data/db/` | Postgres source registry + ingest jobs + IngestPlan ledger |
| `client/lumid_data/client/` | in-process Python client used by lumid apps |
| `mcp_server/` | thin MCP wrapper around `client` (optional) |
| `cli/lumid_data_cli/` | `lumid-data stack` CLI (FlowMesh-shaped) |
| `deploy/` | one-click `docker compose up -d`; `.env.example` |

## OSS stack

| Slot | Choice |
|------|--------|
| Object store | MinIO |
| Table format | Delta Lake (via `deltalake` Python binding) |
| Catalog (primary) | Unity Catalog OSS |
| Catalog (compat) | Hive Metastore (synced mirror) |
| Streaming bus | Redpanda (Kafka API) |
| Streaming SQL | RisingWave |
| Vector store | Milvus |
| Time-series | TimescaleDB |
| Control bus | NATS |
| Op DB | PostgreSQL |
| Compute | FlowMesh (existing) |
| Workflow optimizer | Lumilake (existing) |
| Governance / lineage | FlowMesh (post-refactor #3) |
| Identity | lumid (`/oauth/introspect`) |

## Setup

```bash
pip install uv
uv sync --all-extras                      # all deps
uv run pre-commit run --all-files         # before any commit
uv run pytest tests/                      # unit tests
uv run lumid-data stack up                # full local stack
```

## Hook Plugin Extension Points

Same contract as FlowMesh / Lumilake. Plugins are Python modules with a
top-level `install()` (sync or `@asynccontextmanager async def`).
Loaded from `LUMID_DATA_PLUGINS` env var (CSV of importable module
names) at FastAPI lifespan startup.

- `IdentityProvider` — resolve a bearer token to a principal
  (`auth/security.py`).
- `SourcePolicyHook` — pre-ingest precondition / per-source policy.
- `LineageSink` — fan-out OpenLineage `RunEvent`s after ingest
  (default: NATS publisher). Typical extra consumers: Marquez, DataHub.

Per memory `Lumilake/FlowMesh auth parity`: `LumidIdentityProvider` in
`auth/lumid_introspect.py` mirrors Lumilake's plugin verbatim.

## API Reference (Server: `http://localhost:9100`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/sources` | Register a source (push or pull) |
| GET | `/v1/sources` | List sources |
| GET | `/v1/sources/{id}` | Source details |
| POST | `/v1/sources/{id}/pause` / `/resume` | Toggle a source |
| POST | `/v1/ingest/{source_id}` | Push an ingest payload |
| WS | `/v1/ingest/stream/{source_id}` | Streaming ingest |
| GET | `/v1/datasets` | List datasets |
| GET | `/v1/datasets/{id}` | Dataset details |
| POST | `/v1/admin/dlq/replay` | Replay a DLQ batch |
| GET | `/healthz` | Health check |

## Object Identifiers

3-character type prefixes:
- `src-` source
- `ing-` ingest job
- `pln-` IngestPlan
- `dst-` dataset
- `dlq-` DLQ entry

ID factories live in `src/lumid_data/utils/ids.py`.

## SDK Usage (`lumid_data.client`)

```python
from lumid_data.client import Client

client = Client(base_url="http://localhost:9100", token=os.environ["LUMID_TOKEN"])

# Read
df = client.read_table("system_research.history")

# Ingest
plan_id = client.ingest("expert-memos", payload=open("memo.md").read(), mime="text/markdown")

# Subscribe
async for ev in client.subscribe("DatasetReady"):
    print(ev)
```

## CLI Commands

```
lumid-data stack {up, down, restart, ps, logs, build, pull}
lumid-data source {register, list, get, pause, resume}
lumid-data dlq {list, replay}
lumid-data healthz
```

## Environment Variables

Canonical declared set in `cli/lumid_data_cli/env_schema.py`, mirrored
to `deploy/.env.example`. Run
`uv run scripts/dev/check_env_examples.py --write` after schema edits.

| Variable | Default | Description |
|----------|---------|-------------|
| `LUMID_DATA_HTTP_PORT` | `9100` | Public HTTP port |
| `DATABASE_URL` | – | Postgres connection string |
| `S3_ENDPOINT` | – | MinIO / S3 endpoint |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | – | MinIO credentials |
| `S3_BUCKET` | `lumid-data` | bucket for Delta files + UC volumes |
| `UC_BASE_URL` | – | Unity Catalog REST endpoint |
| `NATS_URL` | – | NATS connection |
| `REDPANDA_BROKERS` | – | Kafka-protocol bootstrap |
| `RISINGWAVE_URL` | – | RW PG-protocol DSN |
| `MILVUS_URI` | – | Milvus connection |
| `TIMESCALE_URL` | – | TimescaleDB DSN |
| `FLOWMESH_GOVERNANCE_URL` | – | FlowMesh governance API base |
| `LUMID_OAUTH_INTROSPECT_URL` | – | lumid introspect endpoint |
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

Mirrors FlowMesh: B113, B202, B310, B324, B506, B607, B614, B701, B108.
Skipped rules and rationale live in `[tool.bandit]`. No bare `# nosec`.

## Commit Conventions

- Single-line subject in imperative mood.
- Conventional prefix: `feat`, `fix`, `refactor`, `chore`, `docs`,
  `style`, `test`. Scope optional: `feat(server): ...`.
- DCO sign-off (`--signoff`) required for code from coding agents.
- One logical change per commit.
- Per memory `Commit hygiene`: only stage files directly related to the
  commit; never `git add -A`.
- Per memory `Lint before commit`: `uv run pre-commit run --all-files`
  before every commit; fix pre-existing failures rather than suppress.

## Pull Requests

PR title format: `type(scope): description`. Allowed types:
`feat, fix, refactor, chore, test, perf, build, ci, docs`. Prefix
`[BREAKING]` for breaking changes.

Per memory `GitHub posting authorization`: never post to GitHub
(open PR / comment / reply) without explicit user approval. Pushing
commits to a feature branch is fine.

## Editing this file

- AGENTS.md stays under 200 lines.
- Don't duplicate other docs; link to the source of truth.
- No code-specific examples — rules describe principles.
- Consolidate conflicts; merge instead of stacking.
