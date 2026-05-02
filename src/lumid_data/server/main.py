"""FastAPI entrypoint. Lifespan loads plugins from LUMID_DATA_PLUGINS."""

import importlib
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from ..catalog.flowmesh_traces import FlowMeshTracesClient
from ..catalog.nats_publisher import NatsPublisher
from ..catalog.unity import UnityClient
from ..db import Base, make_engine, make_sessionmaker
from ..sinks.delta import DeltaSinkConfig
from ..sinks.dlq import DlqSinkConfig
from ..sinks.redpanda import RedpandaConfig, RedpandaProducer
from ..sinks.risingwave import RisingWaveConfig
from .config import load_settings
from .routers import admin
from .routers import catalog as catalog_router
from .routers import health, ingest, sources
from .state import AppState

logger = logging.getLogger("lumid_data.server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())

    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    delta_cfg = DeltaSinkConfig(
        s3_endpoint=settings.s3_endpoint,
        s3_access_key=settings.s3_access_key,
        s3_secret_key=settings.s3_secret_key,
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
    )
    dlq_cfg = DlqSinkConfig(
        s3_endpoint=settings.s3_endpoint,
        s3_access_key=settings.s3_access_key,
        s3_secret_key=settings.s3_secret_key,
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
    )

    unity = UnityClient(base_url=settings.uc_base_url, token=settings.uc_token)
    traces = FlowMeshTracesClient(
        base_url=settings.flowmesh_traces_url,
        token=settings.flowmesh_traces_token,
    )
    nats = NatsPublisher(url=settings.nats_url)
    await nats.connect()

    redpanda: RedpandaProducer | None = None
    risingwave_cfg: RisingWaveConfig | None = None
    if settings.streaming_enabled:
        if settings.redpanda_brokers:
            redpanda = RedpandaProducer(
                RedpandaConfig(bootstrap_servers=settings.redpanda_brokers)
            )
            redpanda.connect()
        if settings.risingwave_dsn:
            risingwave_cfg = RisingWaveConfig(dsn=settings.risingwave_dsn)

    state = AppState(
        settings=settings,
        engine=engine,
        sessionmaker=sessionmaker,
        delta_cfg=delta_cfg,
        dlq_cfg=dlq_cfg,
        unity=unity,
        traces=traces,
        nats=nats,
        redpanda=redpanda,
        risingwave_cfg=risingwave_cfg,
    )
    app.state.app_state = state

    plugin_stack = AsyncExitStack()
    plugin_names = [
        name.strip() for name in settings.plugins.split(",") if name.strip()
    ]
    for name in plugin_names:
        module = importlib.import_module(name)
        installer = getattr(module, "install", None)
        if installer is None:
            logger.warning("plugin %s has no install()", name)
            continue
        result = installer()
        if hasattr(result, "__aenter__"):
            await plugin_stack.enter_async_context(result)
        logger.info("loaded plugin %s", name)

    try:
        yield
    finally:
        await plugin_stack.aclose()
        if redpanda is not None:
            redpanda.close()
        await nats.close()
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="lumid.data",
        version="0.1.0",
        description="Data plane for the lumid + FlowMesh + Lumilake stack.",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(sources.router)
    app.include_router(ingest.router)
    app.include_router(catalog_router.router)
    app.include_router(admin.router)
    return app


app = create_app()
