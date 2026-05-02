"""NATS publisher for DatasetReady + OpenLineage RunEvent envelopes.

Single connection per process, reconnect-managed by ``nats-py``.
Subjects:

- ``lumid.data.dataset.ready`` — ``DatasetReady`` JSON
- ``lumid.data.lineage.run``   — OpenLineage ``RunEvent`` JSON

Subscribers downstream: lumid (research workflow wiring), Lumilake
(planner refresh), and any external lineage tool (Marquez / DataHub) that
speaks the OpenLineage subject.
"""

import json
import logging
from dataclasses import dataclass

import nats
from nats.aio.client import Client as NATSClient

from ..schemas.descriptors import DatasetReady
from ..schemas.lineage import RunEvent

logger = logging.getLogger(__name__)

SUBJECT_DATASET_READY = "lumid.data.dataset.ready"
SUBJECT_LINEAGE_RUN = "lumid.data.lineage.run"


@dataclass
class NatsPublisher:
    url: str
    _client: NATSClient | None = None

    async def connect(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        self._client = await nats.connect(self.url)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.drain()
            self._client = None

    async def publish_dataset_ready(self, event: DatasetReady) -> None:
        if self._client is None:
            raise RuntimeError("NatsPublisher not connected")
        body = event.model_dump_json().encode("utf-8")
        await self._client.publish(SUBJECT_DATASET_READY, body)

    async def publish_lineage(self, event: RunEvent) -> None:
        if self._client is None:
            raise RuntimeError("NatsPublisher not connected")
        body = json.dumps(event.model_dump(mode="json")).encode("utf-8")
        await self._client.publish(SUBJECT_LINEAGE_RUN, body)
