# lumid.data

A **data management service** with two surfaces under one URL:

- **Traditional CRUD** — REST over Postgres (`/db`), S3-compatible
  object storage (`/storage`), plus a SQL passthrough (`/sql`).
- **LLM-driven data agent** — `/agent/v1` orchestrates the same CRUD
  endpoints over an LLM tool-use loop; `/mcp` exposes the same tools to
  any MCP client. Same auth, same audit log, no privileged backdoor.

## Quick start

```bash
# Install
uv sync --extra dev

# Bring up TimescaleDB + MinIO + Redpanda + PostgREST + lumid.data
cp .env.example .env       # then edit values
docker compose up -d

# ...or via the CLI
uv run lumid-data stack up
```

The service comes up at `http://127.0.0.1:9100` (`/docs` for the OpenAPI
browser). Set `LUMID_DATA_LLM_API_KEY` in `.env` to unlock
`/agent/v1`.

```bash
# Direct CRUD
curl http://127.0.0.1:9100/db/v1/users?id=eq.1 -H "Authorization: Bearer $LUMID_TOKEN"
curl -X PUT http://127.0.0.1:9100/storage/v1/object/lumid-data/hello.txt \
  -H "Authorization: Bearer $LUMID_TOKEN" --data 'hello'

# Agent (streaming SSE)
curl http://127.0.0.1:9100/agent/v1 \
  -H "Authorization: Bearer $LUMID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"goal": "how many users do we have?"}'
```

## CLI

```
lumid-data stack {up, down, restart, ps, logs, build, pull}
lumid-data sql query "SELECT 1"
lumid-data storage {get, put, ls, rm}
lumid-data agent run "..."
lumid-data admin {audit, runs, run}
```

## License

Apache License 2.0. See `LICENSE`.
