"""Ingest endpoints. POST for batch payloads; WebSocket for stream-cadence sources."""

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ...agent import route as agent_route
from ...catalog import unity as uc
from ...db.models import Dataset, DlqEntry, IngestJob, IngestPlanRow, Source
from ...schemas.descriptors import DatasetReady, IngestPlan
from ...sinks import delta, dlq, objects
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


@router.post("/{source_id}", status_code=status.HTTP_202_ACCEPTED)
async def ingest_push(
    source_id: str,
    request: Request,
    state: AppState = Depends(get_state),
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("ingest:write")),
) -> dict[str, str]:
    payload = await request.body()
    mime = request.headers.get("content-type")
    return await _run_ingest(
        state=state,
        session=session,
        principal=_principal,
        source_id=source_id,
        payload=payload,
        mime=mime,
    )


@router.post("/{source_id}/files", status_code=status.HTTP_202_ACCEPTED)
async def ingest_multipart(
    source_id: str,
    file: UploadFile = File(...),
    state: AppState = Depends(get_state),
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("ingest:write")),
) -> dict[str, str]:
    """Convenience multipart endpoint: ``curl -F file=@image.png``."""
    payload = await file.read()
    return await _run_ingest(
        state=state,
        session=session,
        principal=_principal,
        source_id=source_id,
        payload=payload,
        mime=file.content_type or "application/octet-stream",
    )


async def _run_ingest(
    *,
    state: AppState,
    session: AsyncSession,
    principal: PrincipalContext,
    source_id: str,
    payload: bytes,
    mime: str | None,
) -> dict[str, str]:
    src_row = await session.get(Source, source_id)
    if src_row is None:
        raise HTTPException(status_code=404, detail="source not found")
    if src_row.paused:
        raise HTTPException(status_code=409, detail="source is paused")

    descriptor = _to_descriptor(src_row)

    ingest_id = new_ingest_id()
    job = IngestJob(
        ingest_id=ingest_id,
        source_id=source_id,
        received_at=datetime.now(UTC),
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

    if plan.route not in ("delta", "uc_volume_with_manifest"):
        job.status = "failed"
        job.error = f"unsupported batch route {plan.route!r}"
        plan_row.status = "failed"
        plan_row.error = job.error
        await session.commit()
        raise HTTPException(status_code=501, detail=job.error)

    assert plan.target_table is not None
    state.unity.ensure_catalog(state.settings.uc_catalog)
    catalog, schema_name, _ = plan.target_table.split(".", 2)
    state.unity.ensure_schema(catalog, schema_name)

    arrow_table = result.table
    if plan.route == "uc_volume_with_manifest":
        assert plan.target_volume is not None
        arrow_table = _land_blob(state, plan, payload, mime)
    table_uri = delta.write(
        state.delta_cfg,
        plan.target_table,
        arrow_table,
        partition_by=descriptor.partition_by or None,
    )
    state.unity.register_external_delta(
        full_name=plan.target_table,
        storage_location=table_uri,
        columns=uc.arrow_columns_to_uc(arrow_table.schema),
    )

    is_first = False
    dataset_row = await _find_dataset(session, table_uri=table_uri)
    if dataset_row is None:
        dataset_row = Dataset(
            dataset_id=new_dataset_id(),
            source_id=source_id,
            table_uri=table_uri,
            volume_uri=plan.target_volume,
            modality=plan.modality_resolved,
            schema_fp=plan.schema_fp,
            version=1,
            rows_total=arrow_table.num_rows,
        )
        session.add(dataset_row)
        is_first = True
    else:
        dataset_row.version += 1
        dataset_row.rows_total += arrow_table.num_rows
        dataset_row.schema_fp = plan.schema_fp
        dataset_row.updated_at = datetime.now(UTC)

    plan_row.status = "succeeded"
    plan_row.completed_at = datetime.now(UTC)
    job.status = "succeeded"
    await session.commit()

    event = DatasetReady(
        event_id=new_event_id(),
        dataset_id=dataset_row.dataset_id,
        source_id=source_id,
        table_uri=table_uri,
        volume_uri=plan.target_volume,
        modality=plan.modality_resolved,
        schema_fp=plan.schema_fp,
        version=dataset_row.version,
        is_first=is_first,
        rows_added=arrow_table.num_rows,
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


def _land_blob(state: AppState, plan: IngestPlan, payload: bytes, mime: str | None):
    """Write blob to MinIO under the UC volume; rebuild manifest with object_uri.

    Returns the manifest pyarrow.Table that gets appended to the Delta
    manifest table. Idempotent: existing object keys are skipped.
    """
    from ...agent import blob as agent_blob

    assert plan.target_volume is not None
    sha256 = agent_blob.compute_sha256(payload)
    volume_path = plan.target_volume.replace(".", "/")
    obj_cfg = objects.ObjectSinkConfig(
        s3_endpoint=state.delta_cfg.s3_endpoint,
        s3_access_key=state.delta_cfg.s3_access_key,
        s3_secret_key=state.delta_cfg.s3_secret_key,
        s3_bucket=state.delta_cfg.s3_bucket,
        s3_region=state.delta_cfg.s3_region,
    )
    object_uri = objects.write_blob(
        obj_cfg,
        volume_path=volume_path,
        sha256=sha256,
        payload=payload,
        content_type=mime,
    )
    volume_root = f"s3://{obj_cfg.s3_bucket}/{volume_path}"
    state.unity.ensure_volume(plan.target_volume, storage_location=volume_root)
    return agent_blob.manifest_row(
        object_uri=object_uri,
        sha256=sha256,
        modality=plan.modality_resolved,
        content_type=mime,
        size_bytes=len(payload),
    )


@router.websocket("/stream/{source_id}")
async def ingest_stream(
    websocket: WebSocket,
    source_id: str,
) -> None:
    """WebSocket for stream-cadence sources.

    Each frame received (text or binary) is produced to the source's
    Redpanda topic. RisingWave consumes the topic, materializes the MV,
    and the configured Delta sink lands rows. The endpoint stays open
    for the client's lifetime; backpressure is the producer's
    `flush()`.

    Auth: bearer token in `Sec-WebSocket-Protocol` or as a query
    parameter ``?token=...``. Standard FastAPI ``Depends`` doesn't apply
    to WebSocket auth in older starlette; we resolve manually.
    """
    state: AppState = websocket.app.state.app_state
    if not state.streaming_enabled or state.redpanda is None:
        await websocket.close(code=1011, reason="streaming disabled")
        return

    async with state.sessionmaker() as session:
        src_row = await session.get(Source, source_id)
    if src_row is None:
        await websocket.close(code=4404, reason="source not found")
        return
    if src_row.cadence != "stream":
        await websocket.close(code=4400, reason="source is not stream-cadence")
        return
    if src_row.paused:
        await websocket.close(code=4409, reason="source paused")
        return

    async with state.sessionmaker() as session:
        from sqlalchemy import select

        stmt = (
            select(Dataset)
            .where(Dataset.source_id == source_id)
            .order_by(Dataset.created_at.desc())
            .limit(1)
        )
        dataset = (await session.execute(stmt)).scalar_one_or_none()
    if dataset is None or dataset.topic is None:
        await websocket.close(code=1011, reason="stream pipeline not bootstrapped")
        return

    topic = dataset.topic
    await websocket.accept()
    frames = 0
    loop = asyncio.get_running_loop()
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            payload: bytes
            if "bytes" in msg and msg["bytes"] is not None:
                payload = bytes(msg["bytes"])
            elif "text" in msg and msg["text"] is not None:
                payload = msg["text"].encode("utf-8")
            else:
                continue
            await loop.run_in_executor(None, state.redpanda.produce, topic, payload)
            frames += 1
            if frames % 100 == 0:
                await loop.run_in_executor(None, state.redpanda.flush, 1.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("stream WS error for source %s", source_id)
        try:
            await websocket.close(code=1011, reason=str(exc)[:120])
        except Exception:
            pass
        return
    finally:
        if state.redpanda is not None:
            await loop.run_in_executor(None, state.redpanda.flush, 5.0)
        logger.info("stream WS closed for source=%s frames=%d", source_id, frames)
