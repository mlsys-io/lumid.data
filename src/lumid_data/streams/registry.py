"""CRUD on stream_sources / stream_runs.

Pure SQLAlchemy. Routers import these; the runner subscribes to them
on lifespan startup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import StreamRun, StreamSource
from ..utils.ids import new_stream_id, new_stream_run_id

VALID_TRANSPORTS = {"webhook", "websocket", "kafka"}
VALID_STATES = {"active", "paused", "stopped"}


async def register(
    session: AsyncSession,
    *,
    name: str,
    transport: str,
    config: dict[str, Any],
    sink: dict[str, Any],
    created_by: str,
) -> StreamSource:
    if transport not in VALID_TRANSPORTS:
        raise ValueError(f"unknown transport {transport!r}")
    row = StreamSource(
        id=new_stream_id(),
        name=name,
        transport=transport,
        config=config,
        sink=sink,
        state="paused",
        created_by=created_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_sources(session: AsyncSession) -> list[StreamSource]:
    rows = (await session.execute(select(StreamSource))).scalars().all()
    return list(rows)


async def get(session: AsyncSession, source_id: str) -> StreamSource | None:
    return await session.get(StreamSource, source_id)


async def set_state(
    session: AsyncSession, source_id: str, state: str
) -> StreamSource | None:
    if state not in VALID_STATES:
        raise ValueError(f"unknown state {state!r}")
    row = await session.get(StreamSource, source_id)
    if row is None:
        return None
    row.state = state
    await session.commit()
    await session.refresh(row)
    return row


async def open_run(session: AsyncSession, source_id: str) -> StreamRun:
    row = StreamRun(id=new_stream_run_id(), source_id=source_id)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def close_run(
    session: AsyncSession,
    run_id: str,
    *,
    status: str,
    last_error: str | None = None,
) -> None:
    row = await session.get(StreamRun, run_id)
    if row is None:
        return
    row.ended_at = datetime.now(UTC)
    row.status = status
    if last_error is not None:
        row.last_error = last_error
    await session.commit()


async def bump_run(
    session: AsyncSession,
    run_id: str,
    *,
    processed: int = 0,
    errors: int = 0,
    last_offset: dict[str, Any] | None = None,
) -> None:
    row = await session.get(StreamRun, run_id)
    if row is None:
        return
    row.processed_count += processed
    row.error_count += errors
    if last_offset is not None:
        row.last_offset = last_offset
    await session.commit()
