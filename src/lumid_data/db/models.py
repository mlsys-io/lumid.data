"""SQLAlchemy ORM models for lumid_data_meta: audit_log, agent_runs."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


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
    request_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


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
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
