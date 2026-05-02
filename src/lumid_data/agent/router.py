"""The data agent: orchestrates the four-stage loop and returns an IngestPlan.

Stages:

1. Perception — ``modality.classify``
2. Planning — ``decisions.decide`` (policy table)
3. Grounding — ``schema.infer`` + ``schema.reconcile`` + ``quality.check``
4. Execution — performed by the caller (sinks/) using the returned plan;
   this module does not touch the sinks.

The plan is the only thing this module returns. Sink execution is the
caller's responsibility, so the agent stays pure (deterministic, easy
to test). Replay = re-run `route()` with the same inputs.

Stream-cadence sources don't have per-payload plans (each frame just
produces to a topic). ``plan_for_stream_source()`` returns a single
plan at registration time describing the pipeline (topic, target Delta
table); subsequent frames reference it.
"""

import logging
from datetime import UTC, datetime

import pyarrow as pa

from ..schemas.descriptors import IngestPlan, Modality, SourceDescriptor
from ..utils.ids import new_plan_id
from . import blob, decisions, modality, quality, schema

_BLOB_MODALITIES: set[Modality] = {"image", "audio", "video", "blob"}

logger = logging.getLogger(__name__)


class AgentResult:
    __slots__ = ("plan", "table")

    def __init__(self, plan: IngestPlan, table: pa.Table | None) -> None:
        self.plan = plan
        self.table = table


def route(
    payload: bytes,
    descriptor: SourceDescriptor,
    *,
    ingest_id: str,
    mime_override: str | None = None,
    existing_schema: pa.Schema | None = None,
) -> AgentResult:
    """Run the four-stage loop and return an ``IngestPlan`` + the prepared Arrow table.

    The table is ``None`` when the route is DLQ (no usable data).
    """
    received_at = datetime.now(UTC)
    resolved = modality.classify(payload, descriptor, mime_override=mime_override)

    if resolved in _BLOB_MODALITIES:
        return _route_blob(
            payload=payload,
            descriptor=descriptor,
            ingest_id=ingest_id,
            received_at=received_at,
            resolved=resolved,
            mime_override=mime_override,
        )

    try:
        table = schema.infer(
            payload, resolved, mime_hint=mime_override or descriptor.mime_hint
        )
    except Exception as exc:
        logger.warning("schema inference failed for ingest_id=%s: %s", ingest_id, exc)
        plan = IngestPlan(
            plan_id=new_plan_id(),
            ingest_id=ingest_id,
            source_id=descriptor.source_id or "",
            received_at=received_at,
            modality_resolved=resolved,
            route="dlq",
            status="dlq",
            error=f"schema_inference_failed: {exc}",
        )
        return AgentResult(plan=plan, table=None)

    schema_fp = schema.fingerprint(table.schema)
    is_drift, drift_issues = schema.reconcile(table.schema, existing_schema)

    table, qreport = quality.check(table, descriptor)
    qreport.schema_drift = is_drift
    qreport.issues.extend(drift_issues)

    if descriptor.policy.require_non_empty and table.num_rows == 0:
        plan = IngestPlan(
            plan_id=new_plan_id(),
            ingest_id=ingest_id,
            source_id=descriptor.source_id or "",
            received_at=received_at,
            modality_resolved=resolved,
            route="dlq",
            schema_fp=schema_fp,
            quality_report=qreport,
            status="dlq",
            error="empty_after_quality",
        )
        return AgentResult(plan=plan, table=None)

    decision = decisions.decide(descriptor, resolved)
    plan = IngestPlan(
        plan_id=new_plan_id(),
        ingest_id=ingest_id,
        source_id=descriptor.source_id or "",
        received_at=received_at,
        modality_resolved=resolved,
        route=decision.route,
        target_table=decision.target_table,
        target_volume=decision.target_volume,
        target_topic=decision.target_topic,
        schema_fp=schema_fp,
        quality_report=qreport,
        policy_applied=descriptor.policy.model_dump(),
        status="pending",
    )
    return AgentResult(plan=plan, table=table)


def _route_blob(
    *,
    payload: bytes,
    descriptor: SourceDescriptor,
    ingest_id: str,
    received_at: datetime,
    resolved: Modality,
    mime_override: str | None,
) -> AgentResult:
    """Blob path: skip schema inference; build manifest row and route to UC volume."""
    from ..schemas.descriptors import QualityReport

    sha256 = blob.compute_sha256(payload)
    content_type = mime_override or descriptor.mime_hint
    manifest = blob.manifest_row(
        object_uri="",  # filled in by the sink after upload
        sha256=sha256,
        modality=resolved,
        content_type=content_type,
        size_bytes=len(payload),
    )
    qreport = QualityReport(rows_in=1, rows_out=1)
    decision = decisions.decide(descriptor, resolved)
    plan = IngestPlan(
        plan_id=new_plan_id(),
        ingest_id=ingest_id,
        source_id=descriptor.source_id or "",
        received_at=received_at,
        modality_resolved=resolved,
        route=decision.route,
        target_table=decision.target_table,
        target_volume=decision.target_volume,
        target_topic=decision.target_topic,
        schema_fp=schema.fingerprint(blob.MANIFEST_SCHEMA),
        quality_report=qreport,
        policy_applied=descriptor.policy.model_dump(),
        status="pending",
    )
    return AgentResult(plan=plan, table=manifest)


def plan_for_stream_source(descriptor: SourceDescriptor) -> IngestPlan:
    """Produce a one-shot plan for a stream-cadence source at registration time.

    Streaming sources don't have per-payload plans. This single plan
    captures the pipeline shape (topic, target Delta table) so the
    bootstrap can provision the Redpanda topic + RW source/MV/sink
    deterministically. Frames pushed via the WS endpoint reference this
    plan_id.
    """
    if descriptor.cadence != "stream":
        raise ValueError("plan_for_stream_source requires cadence='stream'")
    resolved: Modality = (
        descriptor.modality if descriptor.modality != "auto" else "structured"
    )
    decision = decisions.decide(descriptor, resolved)
    return IngestPlan(
        plan_id=new_plan_id(),
        ingest_id="ing-stream",
        source_id=descriptor.source_id or "",
        received_at=datetime.now(UTC),
        modality_resolved=resolved,
        route=decision.route,
        target_table=decision.target_table,
        target_volume=decision.target_volume,
        target_topic=decision.target_topic,
        policy_applied=descriptor.policy.model_dump(),
        status="pending",
    )
