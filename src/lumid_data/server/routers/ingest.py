"""Ingest endpoints. Phase 1: push (POST). Streaming WS deferred to Phase 2."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...agent import route as agent_route
from ...catalog import unity as uc
from ...db.models import Dataset, DlqEntry, IngestJob, IngestPlanRow, Source
from ...schemas.descriptors import DatasetReady
from ...sinks import delta, dlq
from ...utils.ids import (
    new_dataset_id,
    new_dlq_id,
    new_event_id,
    new_ingest_id,
)
from ..auth.security import PrincipalContext, require_scope
from ..deps import get_session, get_state
from ..state import AppState
from .sources import _to_descriptor

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


@router.post("/{source_id}", status_code=status.HTTP_202_ACCEPTED)
async def ingest_push(
    source_id: str,
    request: Request,
    state: AppState = Depends(get_state),
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("ingest:write")),
) -> dict[str, str]:
    src_row = await session.get(Source, source_id)
    if src_row is None:
        raise HTTPException(status_code=404, detail="source not found")
    if src_row.paused:
        raise HTTPException(status_code=409, detail="source is paused")

    descriptor = _to_descriptor(src_row)
    payload = await request.body()
    mime = request.headers.get("content-type")

    ingest_id = new_ingest_id()
    job = IngestJob(
        ingest_id=ingest_id,
        source_id=source_id,
        received_at=datetime.now(timezone.utc),
        payload_size=len(payload),
        payload_mime=mime,
        status="pending",
    )
    session.add(job)
    await session.flush()

    result = agent_route(payload, descriptor, ingest_id=ingest_id, mime_override=mime)
    plan = result.plan

    plan_row = IngestPlanRow(
        plan_id=plan.plan_id,
        ingest_id=ingest_id,
        source_id=source_id,
        received_at=plan.received_at,
        modality_resolved=plan.modality_resolved,
        route=plan.route,
        target_table=plan.target_table,
        target_volume=plan.target_volume,
        target_topic=plan.target_topic,
        schema_fp=plan.schema_fp,
        quality_report=plan.quality_report.model_dump(),
        policy_applied=plan.policy_applied,
        status=plan.status,
        error=plan.error,
    )
    session.add(plan_row)
    job.plan_id = plan.plan_id

    if plan.route == "dlq" or result.table is None:
        dlq_id = new_dlq_id()
        payload_uri = dlq.store_payload(state.dlq_cfg, dlq_id, payload, mime)
        session.add(
            DlqEntry(
                dlq_id=dlq_id,
                ingest_id=ingest_id,
                source_id=source_id,
                received_at=plan.received_at,
                reason=plan.error or "agent routed to DLQ",
                payload_uri=payload_uri,
                quality_report=plan.quality_report.model_dump(),
            )
        )
        plan_row.status = "dlq"
        job.status = "dlq"
        job.error = plan.error
        await session.commit()
        return {"ingest_id": ingest_id, "plan_id": plan.plan_id, "status": "dlq"}

    if plan.route != "delta":
        job.status = "failed"
        job.error = f"Phase 1 only supports route=delta (got {plan.route!r})"
        plan_row.status = "failed"
        plan_row.error = job.error
        await session.commit()
        raise HTTPException(status_code=501, detail=job.error)

    assert plan.target_table is not None
    state.unity.ensure_catalog(state.settings.uc_catalog)
    catalog, schema_name, _ = plan.target_table.split(".", 2)
    state.unity.ensure_schema(catalog, schema_name)
    table_uri = delta.write(
        state.delta_cfg,
        plan.target_table,
        result.table,
        partition_by=descriptor.partition_by or None,
    )
    state.unity.register_external_delta(
        full_name=plan.target_table,
        storage_location=table_uri,
        columns=uc.arrow_columns_to_uc(result.table.schema),
    )

    is_first = False
    dataset_row = await _find_dataset(session, table_uri=table_uri)
    if dataset_row is None:
        dataset_row = Dataset(
            dataset_id=new_dataset_id(),
            source_id=source_id,
            table_uri=table_uri,
            modality=plan.modality_resolved,
            schema_fp=plan.schema_fp,
            version=1,
            rows_total=result.table.num_rows,
        )
        session.add(dataset_row)
        is_first = True
    else:
        dataset_row.version += 1
        dataset_row.rows_total += result.table.num_rows
        dataset_row.schema_fp = plan.schema_fp
        dataset_row.updated_at = datetime.now(timezone.utc)

    plan_row.status = "succeeded"
    plan_row.completed_at = datetime.now(timezone.utc)
    job.status = "succeeded"
    await session.commit()

    state.governance.register_dataset(
        {
            "dataset_id": dataset_row.dataset_id,
            "source_id": source_id,
            "table_uri": table_uri,
            "modality": plan.modality_resolved,
            "schema_fp": plan.schema_fp,
            "owner": _principal.principal_id,
        }
    )
    event = DatasetReady(
        event_id=new_event_id(),
        dataset_id=dataset_row.dataset_id,
        source_id=source_id,
        table_uri=table_uri,
        modality=plan.modality_resolved,
        schema_fp=plan.schema_fp,
        version=dataset_row.version,
        is_first=is_first,
        rows_added=result.table.num_rows,
    )
    await state.nats.publish_dataset_ready(event)

    return {
        "ingest_id": ingest_id,
        "plan_id": plan.plan_id,
        "dataset_id": dataset_row.dataset_id,
        "status": "succeeded",
    }


async def _find_dataset(session: AsyncSession, *, table_uri: str) -> Dataset | None:
    from sqlalchemy import select

    stmt = select(Dataset).where(Dataset.table_uri == table_uri)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
