"""/v1/streams + /v1/ingest endpoints.

Endpoints auto-flow into the agent's tool catalog and the MCP server,
so the data agent can register, start, monitor, and replay streams
through normal HTTP.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import StreamDlq, StreamRun
from ...streams import registry as stream_registry
from ...streams import sinks
from ...streams.base import StreamMessage
from ..auth.security import default_principal
from ..deps import get_session, get_state
from ..state import AppState

router = APIRouter(tags=["streams"])


class SinkSpec(BaseModel):
    kind: Literal["postgres_table", "s3_object"]
    schema_: str = Field("public", alias="schema")
    table: str | None = None
    bucket: str | None = None
    prefix: str | None = None
    hypertable: bool = False
    time_column: str = "received_at"


class RegisterRequest(BaseModel):
    name: str
    transport: Literal["webhook", "websocket", "kafka"]
    config: dict[str, Any] = Field(default_factory=dict)
    sink: SinkSpec


class StateChangeRequest(BaseModel):
    state: Literal["active", "paused", "stopped"]


@router.post("/v1/streams", status_code=201)
async def register_stream(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    sink_dict = body.sink.model_dump(by_alias=True)
    if body.sink.kind == "postgres_table":
        if not body.sink.table:
            raise HTTPException(400, "postgres_table sink requires 'table'")
        await sinks.ensure_postgres_landing(sink_dict, state.engine)
    elif body.sink.kind == "s3_object":
        if not body.sink.bucket:
            raise HTTPException(400, "s3_object sink requires 'bucket'")
    row = await stream_registry.register(
        session,
        name=body.name,
        transport=body.transport,
        config=body.config,
        sink=sink_dict,
        created_by=default_principal().principal_id,
    )
    return _serialize(row)


@router.get("/v1/streams")
async def list_streams(
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return [_serialize(r) for r in await stream_registry.list_sources(session)]


@router.get("/v1/streams/{source_id}")
async def get_stream(
    source_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await stream_registry.get(session, source_id)
    if row is None:
        raise HTTPException(404, "stream not found")
    return _serialize(row)


@router.post("/v1/streams/{source_id}/state")
async def set_stream_state(
    source_id: str,
    body: StateChangeRequest,
    session: AsyncSession = Depends(get_session),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    row = await stream_registry.set_state(session, source_id, body.state)
    if row is None:
        raise HTTPException(404, "stream not found")
    if state.stream_runner is not None:
        if body.state == "active" and row.transport == "kafka":
            state.stream_runner.spawn(source_id)
        else:
            await state.stream_runner.stop(source_id)
    return _serialize(row)


@router.get("/v1/streams/{source_id}/status")
async def stream_status(
    source_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    src = await stream_registry.get(session, source_id)
    if src is None:
        raise HTTPException(404, "stream not found")
    stmt = (
        select(StreamRun)
        .where(StreamRun.source_id == source_id)
        .order_by(StreamRun.started_at.desc())
        .limit(1)
    )
    last_run = (await session.execute(stmt)).scalar_one_or_none()
    return {
        "id": src.id,
        "name": src.name,
        "transport": src.transport,
        "state": src.state,
        "last_run": _serialize_run(last_run) if last_run else None,
    }


@router.post("/v1/ingest/{source_id}")
async def ingest_webhook(
    source_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    src = await stream_registry.get(session, source_id)
    if src is None:
        raise HTTPException(404, "stream not found")
    if src.transport != "webhook":
        raise HTTPException(409, f"stream {source_id} is not a webhook")
    if src.state != "active":
        raise HTTPException(409, f"stream {source_id} is {src.state}")
    payload = await request.json()
    if not isinstance(payload, dict):
        payload = {"value": payload}
    msg = StreamMessage(
        payload=payload,
        headers={
            k.lower(): v
            for k, v in request.headers.items()
            if k.lower().startswith("x-")
        },
        offset={"received_at": datetime.now(UTC).isoformat()},
    )
    await state.stream_runner.deliver(src.sink, msg, source_id)
    return {"status": "accepted"}


@router.websocket("/v1/ingest/ws/{source_id}")
async def ingest_websocket(websocket: WebSocket, source_id: str) -> None:
    await websocket.accept()
    state: AppState = websocket.app.state.app_state
    async with state.sessionmaker() as session:
        src = await stream_registry.get(session, source_id)
        if src is None or src.transport != "websocket" or src.state != "active":
            await websocket.close(code=4409, reason="stream not ready")
            return
    seq = 0
    while True:
        try:
            payload = await websocket.receive_json()
        except Exception:
            break
        if not isinstance(payload, dict):
            payload = {"value": payload}
        msg = StreamMessage(payload=payload, offset={"seq": seq})
        seq += 1
        try:
            await state.stream_runner.deliver(src.sink, msg, source_id)
        except Exception as exc:  # delivery failure already DLQ'd
            await websocket.send_json({"error": repr(exc)})


@router.get("/v1/streams/{source_id}/dlq")
async def list_dlq(
    source_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = (
        select(StreamDlq)
        .where(StreamDlq.source_id == source_id)
        .order_by(StreamDlq.received_at.desc())
        .limit(200)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_dlq(r) for r in rows]


@router.post("/v1/streams/dlq/{dlq_id}/replay")
async def replay_dlq(
    dlq_id: str,
    session: AsyncSession = Depends(get_session),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    row = await session.get(StreamDlq, dlq_id)
    if row is None:
        raise HTTPException(404, "dlq entry not found")
    src = await stream_registry.get(session, row.source_id)
    if src is None:
        raise HTTPException(409, "source removed; cannot replay")
    msg = StreamMessage(
        payload=row.payload, headers=row.headers, offset={"replay": dlq_id}
    )
    await state.stream_runner.deliver(src.sink, msg, row.source_id)
    row.replayed = True
    await session.commit()
    return {"status": "replayed"}


def _serialize(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "transport": row.transport,
        "state": row.state,
        "config": row.config,
        "sink": row.sink,
        "created_at": row.created_at.isoformat(),
        "created_by": row.created_by,
    }


def _serialize_run(row: StreamRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "started_at": row.started_at.isoformat(),
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "status": row.status,
        "processed_count": row.processed_count,
        "error_count": row.error_count,
        "last_offset": row.last_offset,
        "last_error": row.last_error,
    }


def _serialize_dlq(row: StreamDlq) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_id": row.source_id,
        "received_at": row.received_at.isoformat(),
        "payload": row.payload,
        "headers": row.headers,
        "error": row.error,
        "replayed": row.replayed,
    }
