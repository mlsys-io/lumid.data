"""FlowMesh trace-upload client (post-PR-#3 governance shape).

FlowMesh's governance is OTel-trace-shaped — per-task ``spans.jsonl``,
``assets.jsonl``, ``lineage.jsonl`` rows uploaded under
``POST /api/v1/traces/tasks/{task_id}/{trace_type}`` and read back via
``GET /api/v1/traces/workflows/{wf}/{trace_type}`` /
``GET /api/v1/traces/workflows/analyze/{wf}``.

This client is **task-scoped**: it can only emit trace rows when the
caller has a ``task_id`` (e.g., ingestion that runs inside a FlowMesh
workflow context). The cross-service "I created a dataset" notification
is published over NATS as ``DatasetReady`` and consumed by Lumilake's
``server/dataplane`` subscriber plugin — no FlowMesh write is required
for that handoff.

When ``FLOWMESH_TRACES_URL`` is unset, the client is a no-op (debug log
only). That's the common path for un-attributed ingest.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_VALID_TRACE_TYPES = {"spans", "assets", "lineage"}


@dataclass(frozen=True)
class FlowMeshTracesClient:
    base_url: str | None
    token: str | None = None

    def _enabled(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def upload(self, task_id: str, trace_type: str, rows: list[dict[str, Any]]) -> None:
        if trace_type not in _VALID_TRACE_TYPES:
            raise ValueError(
                f"trace_type must be one of {_VALID_TRACE_TYPES}; got {trace_type!r}"
            )
        if not self._enabled():
            logger.debug(
                "flowmesh traces (mock): task_id=%s type=%s rows=%d",
                task_id,
                trace_type,
                len(rows),
            )
            return
        body = b"\n".join(json.dumps(row).encode("utf-8") for row in rows)
        files = {"file": (f"{trace_type}.jsonl", body, "application/x-ndjson")}
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(
                f"{self.base_url}/api/v1/traces/tasks/{task_id}/{trace_type}",
                files=files,
                headers=self._headers(),
            )
            if r.status_code in (200, 201):
                return
            logger.warning(
                "flowmesh traces upload failed: %s %s", r.status_code, r.text
            )
            r.raise_for_status()

    def upload_lineage(self, task_id: str, edges: list[dict[str, Any]]) -> None:
        self.upload(task_id, "lineage", edges)

    def upload_assets(self, task_id: str, assets: list[dict[str, Any]]) -> None:
        self.upload(task_id, "assets", assets)
