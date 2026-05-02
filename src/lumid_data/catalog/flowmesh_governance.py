"""FlowMesh governance client.

After FlowMesh PR #3 lands, governance (lineage, policy, audit) lives in
FlowMesh, not Lumilake. This client POSTs dataset registrations and
lineage edges to that API.

Until PR #3 is merged, the client tolerates a missing endpoint via
``LUMID_DATA_GOVERNANCE_MODE=mock`` (configured by the caller); calls
are then no-ops that log at debug level.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..schemas.lineage import RunEvent

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


@dataclass(frozen=True)
class GovernanceClient:
    base_url: str | None
    token: str | None = None
    mode: str = "live"  # "live" or "mock"

    def _enabled(self) -> bool:
        return bool(self.base_url) and self.mode != "mock"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def register_dataset(self, payload: dict[str, Any]) -> None:
        if not self._enabled():
            logger.debug("governance mock: register_dataset payload=%s", payload)
            return
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(
                f"{self.base_url}/api/v1/governance/datasets",
                json=payload,
                headers=self._headers(),
            )
            if r.status_code in (200, 201):
                return
            logger.warning("governance register_dataset failed: %s %s", r.status_code, r.text)
            r.raise_for_status()

    def append_lineage(self, event: RunEvent) -> None:
        if not self._enabled():
            logger.debug("governance mock: append_lineage event=%s", event.model_dump())
            return
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(
                f"{self.base_url}/api/v1/governance/lineage",
                json=event.model_dump(mode="json"),
                headers=self._headers(),
            )
            if r.status_code in (200, 201):
                return
            logger.warning("governance append_lineage failed: %s %s", r.status_code, r.text)
            r.raise_for_status()
