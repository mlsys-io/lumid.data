# lumid.data

Data plane for the lumid + FlowMesh + Lumilake stack. Turns raw,
multi-modal, streaming-or-batched inputs into typed, governed, queryable
assets that Lumilake can plan over and FlowMesh can scan.

## What's in here

- A FastAPI service (`src/lumid_data/`) that runs the data agent and
  routes ingest payloads to the right sink.
- An in-process Python client (`client/`) that lumid apps import.
- A `lumid-data` CLI (`cli/`) shaped like `flowmesh stack`.
- A one-click Compose stack (`deploy/docker-compose.yml`) that brings up
  this service plus FlowMesh, Lumilake, and the shared infra.

## Quick start

```bash
# Install
uv sync --all-extras

# Bring up the full stack (data plane + FlowMesh + Lumilake + infra)
uv run lumid-data stack up

# ...or directly
cd deploy && docker compose up -d
```

The data plane comes up at `http://127.0.0.1:9100` (`/docs` for the API
browser).

## Architecture

See the design doc and the OSS stack table in `AGENTS.md`. The four-stage
data agent loop (Perception → Planning → Grounding → Execution) follows
Sun et al., *Autonomous Data Agents* (arXiv 2509.18710).

## License

Apache License 2.0. See `LICENSE`.
