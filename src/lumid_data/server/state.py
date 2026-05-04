"""Shared app state attached to the FastAPI app on startup."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .config import Settings
from .services.audit import AuditWriter
from .services.postgrest_jwt import PostgrestJwtConfig
from .services.s3 import S3Config


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    s3_cfg: S3Config
    s3_client: Any
    postgrest_jwt: PostgrestJwtConfig
    audit: AuditWriter
    llm_adapter: Any = None  # LLMAdapter; populated when /agent ships
    stream_runner: Any = None  # streams.StreamRunner
