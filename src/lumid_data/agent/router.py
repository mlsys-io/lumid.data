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
"""

import logging
from datetime import datetime, timezone

import pyarrow as pa

from ..schemas.descriptors import IngestPlan, SourceDescriptor
from ..utils.ids import new_plan_id
from . import decisions, modality, quality, schema

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
    received_at = datetime.now(timezone.utc)
    resolved = modality.classify(payload, descriptor, mime_override=mime_override)

    try:
        table = schema.infer(payload, resolved, mime_hint=mime_override or descriptor.mime_hint)
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
