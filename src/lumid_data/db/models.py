"""SQLAlchemy ORM models for lumid_data_meta.

Tables: audit_log, agent_runs, stream_sources, stream_runs, stream_dlq.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditLog(Base):
    """One row per CRUD call (db / storage / sql / agent / mcp).

    Source of truth for "who did what when". Optional NATS fan-out is a
    side-effect, not a substitute.
    """

    __tablename__ = "audit_log"
    __table_args__ = {"schema": "lumid_data_meta"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    principal_id: Mapped[str] = mapped_column(String(128), index=True)
    org_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    surface: Mapped[str] = mapped_column(
        String(16), index=True
    )  # db|storage|sql|agent|mcp
    op: Mapped[str] = mapped_column(String(16))  # GET|PUT|POST|DELETE|...
    path: Mapped[str] = mapped_column(String(1024))
    status_code: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class AgentRun(Base):
    """One row per /agent/v1 invocation; transcripts kept for replay + audit."""

    __tablename__ = "agent_runs"
    __table_args__ = {"schema": "lumid_data_meta"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    principal_id: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="running"
    )  # running|done|failed|aborted
    steps_taken: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    final_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class StreamSource(Base):
    """A registered streaming source descriptor.

    The same row covers webhook (passive), websocket (server-side push
    endpoint), and kafka (active consumer). ``transport`` decides which
    adapter the runner spawns; ``config`` carries transport-specific
    fields (kafka topic, group, brokers; ws path; webhook path); ``sink``
    decides where messages land (postgres table or s3 prefix). The
    ``state`` column is the user-facing on/off switch.
    """

    __tablename__ = "stream_sources"
    __table_args__ = {"schema": "lumid_data_meta"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    transport: Mapped[str] = mapped_column(String(16))  # webhook|websocket|kafka
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    sink: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    state: Mapped[str] = mapped_column(
        String(16), default="paused"
    )  # active|paused|stopped
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    created_by: Mapped[str] = mapped_column(String(128))


class StreamRun(Base):
    """One row per runner attempt for a source. Tracks offset + lag + errors."""

    __tablename__ = "stream_runs"
    __table_args__ = {"schema": "lumid_data_meta"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="running"
    )  # running|stopped|failed
    last_offset: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class StreamDlq(Base):
    """Failed payloads parked for replay."""

    __tablename__ = "stream_dlq"
    __table_args__ = {"schema": "lumid_data_meta"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    headers: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    error: Mapped[str] = mapped_column(Text)
    replayed: Mapped[bool] = mapped_column(Boolean, default=False)
