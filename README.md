# lumid.data

A **data management service** with two surfaces under one URL:

- **Traditional CRUD** — raw SQL over Postgres (`/sql`) and
  S3-compatible object storage (`/storage`).
- **LLM-driven data agent** — `/agent/v1` orchestrates the same CRUD
  endpoints plus deterministic schema-card and replay/materialization
  tools over an LLM tool-use loop; `/mcp` exposes the CRUD tools to any
  MCP client. Same auth, same audit log, no privileged backdoor.

## Quick start

```bash
# Install
uv sync --all-packages --group dev

# App against an external Postgres + S3 (URLs from .env)
cp .env.example .env       # then edit values
docker compose up -d

# Or bring up the full bundled demo stack (Postgres + MinIO + Redpanda)
docker compose --profile bundled up -d

# ...or via the CLI
uv run lumid-data stack up
```

The service comes up at `http://127.0.0.1:9100` (`/docs` for the OpenAPI
browser). Set `LUMID_DATA_LLM_API_KEY` in `.env` to unlock
`/agent/v1`.

```bash
# Direct CRUD (auth is a no-op by default; the Authorization header is
# optional unless an IdentityProvider plugin is registered).
curl -X POST http://127.0.0.1:9100/sql/v1 \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT count(*) FROM users"}'
curl -X PUT http://127.0.0.1:9100/storage/v1/object/lumid-data/hello.txt --data 'hello'

# Agent (streaming SSE)
curl http://127.0.0.1:9100/agent/v1 \
  -H "Content-Type: application/json" \
  -d '{"goal": "how many users do we have?", "skills": ["data_retrieval"]}'
```

## CLI

```
lumid-data stack {up, down, restart, ps, logs, build, pull}
lumid-data sql query "SELECT 1"
lumid-data storage {get, put, ls, rm}
lumid-data agent run "..." --skill data_retrieval
lumid-data admin {audit, runs, run}
```

## License

Apache License 2.0. See `LICENSE`.
