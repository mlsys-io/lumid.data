"""Pydantic schemas for source descriptors, ingest plans, and dataset events."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Cadence = Literal["batch", "stream", "pull"]
Modality = Literal[
    "auto",
    "text",
    "structured",
    "image",
    "audio",
    "video",
    "timeseries",
    "embedding",
    "blob",
]
Route = Literal[
    "delta",
    "rw_stream",
    "uc_volume_with_manifest",
    "milvus_embed",
    "timescale",
    "dlq",
]
PlanStatus = Literal["pending", "executing", "succeeded", "failed", "dlq"]


class SourcePolicy(BaseModel):
    """Per-source ingest policy."""

    model_config = ConfigDict(extra="forbid")

    null_threshold: float = Field(0.5, ge=0.0, le=1.0)
    require_non_empty: bool = True
    schema_evolution: Literal["additive", "strict", "permissive"] = "additive"
    retention_days: int | None = None
    override_route: Route | None = None


class PullConfig(BaseModel):
    """Configuration for pull-cadence sources."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["s3_watch", "rest_poll", "cdc_pg", "file_drop"]
    location: str
    schedule_cron: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class SourceDescriptor(BaseModel):
    """What lumid (or any external producer) posts to register a source."""

    model_config = ConfigDict(extra="forbid")

    source_id: str | None = None
    name: str
    tenant: str
    app: str | None = None
    cadence: Cadence
    modality: Modality = "auto"
    mime_hint: str | None = None
    embed_with: str | None = None
    partition_by: list[str] = Field(default_factory=list)
    dedup_keys: list[str] = Field(default_factory=list)
    policy: SourcePolicy = Field(default_factory=SourcePolicy)
    pull_config: PullConfig | None = None
    paused: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QualityReport(BaseModel):
    """Result of grounding-stage quality gates."""

    model_config = ConfigDict(extra="forbid")

    rows_in: int = 0
    rows_out: int = 0
    rows_dropped: int = 0
    null_ratio: float = 0.0
    duplicate_ratio: float = 0.0
    schema_drift: bool = False
    issues: list[str] = Field(default_factory=list)


class IngestPlan(BaseModel):
    """The agent's deterministic plan for a single payload, recorded before sinks run."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    ingest_id: str
    source_id: str
    received_at: datetime
    modality_resolved: Modality
    route: Route
    target_table: str | None = None
    target_volume: str | None = None
    target_topic: str | None = None
    schema_fp: str | None = None
    quality_report: QualityReport = Field(default_factory=QualityReport)
    policy_applied: dict[str, Any] = Field(default_factory=dict)
    status: PlanStatus = "pending"
    error: str | None = None


class DatasetReady(BaseModel):
    """NATS event published the first time a dataset is created (and on subsequent appends as configured)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    dataset_id: str
    source_id: str
    table_uri: str | None = None
    volume_uri: str | None = None
    topic: str | None = None
    modality: Modality
    schema_fp: str | None = None
    version: int = 1
    is_first: bool = True
    rows_added: int | None = None
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
