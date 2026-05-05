"""FastAPI entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..agent import make_adapter
from ..db import Base, make_engine, make_sessionmaker
from ..streams.runner import StreamRunner
from .config import load_settings
from .routers import (
    admin,
    agent,
    db_proxy,
    health,
    mcp_mount,
    sql,
    storage,
    streams,
)
from .services.audit import AuditWriter, connect_nats
from .services.postgrest_jwt import PostgrestJwtConfig
from .services.s3 import S3Config
from .services.s3 import make_client as make_s3_client
from .state import AppState

logger = logging.getLogger("lumid_data.server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())

    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)

    async with engine.begin() as conn:
        await conn.execute(_create_meta_schema_stmt())
        await conn.run_sync(Base.metadata.create_all)

    s3_cfg = S3Config(
        endpoint=settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        default_bucket=settings.s3_default_bucket,
    )
    s3_client = make_s3_client(s3_cfg)

    postgrest_jwt = PostgrestJwtConfig(
        secret=settings.postgrest_jwt_secret,
        ttl_sec=settings.postgrest_jwt_ttl_sec,
        anon_role=settings.postgrest_anon_role,
        user_role=settings.postgrest_user_role,
        admin_role=settings.postgrest_admin_role,
    )

    nats_client = await connect_nats(settings.nats_url)
    audit = AuditWriter(sessionmaker=sessionmaker, nats_client=nats_client)

    llm_adapter = None
    if settings.llm_api_key:
        llm_adapter = make_adapter(
            settings.llm_provider,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )

    stream_runner = StreamRunner(
        sessionmaker=sessionmaker,
        engine=engine,
        s3_client=s3_client,
        kafka_bootstrap_default=settings.kafka_bootstrap,
    )

    state = AppState(
        settings=settings,
        engine=engine,
        sessionmaker=sessionmaker,
        s3_cfg=s3_cfg,
        s3_client=s3_client,
        postgrest_jwt=postgrest_jwt,
        audit=audit,
        llm_adapter=llm_adapter,
        stream_runner=stream_runner,
    )
    state._fastapi_app = app  # type: ignore[attr-defined]
    app.state.app_state = state

    await stream_runner.start_all_active()

    try:
        yield
    finally:
        await stream_runner.stop_all()
        if nats_client is not None:
            await nats_client.drain()
        if llm_adapter is not None:
            try:
                await llm_adapter.aclose()
            except Exception:
                logger.exception("llm adapter close failed")
        await engine.dispose()


def _create_meta_schema_stmt():
    from sqlalchemy import text

    return text("CREATE SCHEMA IF NOT EXISTS lumid_data_meta")


def create_app() -> FastAPI:
    app = FastAPI(
        title="lumid.data",
        version="0.1.0",
        description=(
            "Data management service: REST CRUD over Postgres + S3 plus an "
            "LLM-driven data agent and an MCP server, all under one URL."
        ),
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(db_proxy.router)
    app.include_router(storage.router)
    app.include_router(sql.router)
    app.include_router(streams.router)
    app.include_router(agent.router)
    app.include_router(admin.router)

    settings = load_settings()
    mcp_mount.mount_mcp(app, base_url=settings.base_url)
    return app
