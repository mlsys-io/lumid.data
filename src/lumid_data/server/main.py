"""FastAPI entrypoint. Lifespan loads plugins from LUMID_DATA_PLUGINS."""

import importlib
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from ..catalog.flowmesh_governance import GovernanceClient
from ..catalog.nats_publisher import NatsPublisher
from ..catalog.unity import UnityClient
from ..db import Base, make_engine, make_sessionmaker
from ..sinks.delta import DeltaSinkConfig
from ..sinks.dlq import DlqSinkConfig
from .config import load_settings
from .routers import admin, catalog as catalog_router, health, ingest, sources
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
    governance = GovernanceClient(
        base_url=settings.flowmesh_governance_url,
        token=settings.flowmesh_governance_token,
        mode=settings.governance_mode,
    )
    nats = NatsPublisher(url=settings.nats_url)
    await nats.connect()

    state = AppState(
        settings=settings,
        engine=engine,
        sessionmaker=sessionmaker,
        delta_cfg=delta_cfg,
        dlq_cfg=dlq_cfg,
        unity=unity,
        governance=governance,
        nats=nats,
    )
    app.state.app_state = state

    plugin_stack = AsyncExitStack()
    plugin_names = [name.strip() for name in settings.plugins.split(",") if name.strip()]
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
