"""SQLAlchemy ORM models for source registry, ingest jobs, IngestPlan ledger, datasets, DLQ."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True)
    app: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    cadence: Mapped[str] = mapped_column(String(16))
    modality: Mapped[str] = mapped_column(String(16), default="auto")
    mime_hint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embed_with: Mapped[str | None] = mapped_column(String(256), nullable=True)
    partition_by: Mapped[list[str]] = mapped_column(JSONB, default=list)
    dedup_keys: Mapped[list[str]] = mapped_column(JSONB, default=list)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    pull_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    ingest_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("sources.source_id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    payload_size: Mapped[int] = mapped_column(Integer, default=0)
    payload_mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestPlanRow(Base):
    __tablename__ = "ingest_plans"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ingest_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    modality_resolved: Mapped[str] = mapped_column(String(16))
    route: Mapped[str] = mapped_column(String(32))
    target_table: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_volume: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_topic: Mapped[str | None] = mapped_column(String(256), nullable=True)
    schema_fp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    policy_applied: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Dataset(Base):
    __tablename__ = "datasets"

    dataset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("sources.source_id"), index=True)
    table_uri: Mapped[str | None] = mapped_column(String(512), nullable=True, unique=True)
    volume_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(256), nullable=True)
    modality: Mapped[str] = mapped_column(String(16))
    schema_fp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class DlqEntry(Base):
    __tablename__ = "dlq_entries"

    dlq_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ingest_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    reason: Mapped[str] = mapped_column(String(256))
    payload_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    replayed: Mapped[bool] = mapped_column(Boolean, default=False)
