"""Shared app state attached to the FastAPI app on startup."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ..catalog.flowmesh_traces import FlowMeshTracesClient
from ..catalog.nats_publisher import NatsPublisher
from ..catalog.unity import UnityClient
from ..sinks.delta import DeltaSinkConfig
from ..sinks.dlq import DlqSinkConfig
from ..sinks.redpanda import RedpandaProducer
from ..sinks.risingwave import RisingWaveConfig
from .config import Settings


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    delta_cfg: DeltaSinkConfig
    dlq_cfg: DlqSinkConfig
    unity: UnityClient
    traces: FlowMeshTracesClient
    nats: NatsPublisher
    redpanda: RedpandaProducer | None = None
    risingwave_cfg: RisingWaveConfig | None = None

    @property
    def streaming_enabled(self) -> bool:
        return self.redpanda is not None and self.risingwave_cfg is not None
