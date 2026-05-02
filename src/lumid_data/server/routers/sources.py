"""Source registry endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import Source
from ...schemas.descriptors import SourceDescriptor
from ...utils.ids import new_source_id
from ..auth.security import PrincipalContext, require_scope
from ..deps import get_session, get_state
from ..services.streaming import bootstrap_stream_source
from ..state import AppState

router = APIRouter(prefix="/v1/sources", tags=["sources"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SourceDescriptor)
async def register_source(
    descriptor: SourceDescriptor,
    state: AppState = Depends(get_state),
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("sources:write")),
) -> SourceDescriptor:
    if descriptor.source_id is None:
        descriptor.source_id = new_source_id()
    row = Source(
        source_id=descriptor.source_id,
        tenant=descriptor.tenant,
        app=descriptor.app,
        name=descriptor.name,
        cadence=descriptor.cadence,
        modality=descriptor.modality,
        mime_hint=descriptor.mime_hint,
        embed_with=descriptor.embed_with,
        partition_by=list(descriptor.partition_by),
        dedup_keys=list(descriptor.dedup_keys),
        policy=descriptor.policy.model_dump(),
        pull_config=(
            descriptor.pull_config.model_dump() if descriptor.pull_config else None
        ),
        paused=descriptor.paused,
    )
    session.add(row)
    await session.flush()
    if descriptor.cadence == "stream":
        await bootstrap_stream_source(state, descriptor, row, session)
    await session.commit()
    return descriptor


@router.get("", response_model=list[SourceDescriptor])
async def list_sources(
    tenant: str | None = None,
    app: str | None = None,
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("sources:read")),
) -> list[SourceDescriptor]:
    stmt = select(Source)
    if tenant:
        stmt = stmt.where(Source.tenant == tenant)
    if app:
        stmt = stmt.where(Source.app == app)
    stmt = stmt.order_by(Source.created_at.desc()).limit(200)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_descriptor(r) for r in rows]


@router.get("/{source_id}", response_model=SourceDescriptor)
async def get_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("sources:read")),
) -> SourceDescriptor:
    row = await session.get(Source, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="source not found")
    return _to_descriptor(row)


@router.post("/{source_id}/pause")
async def pause_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("sources:write")),
) -> dict[str, str]:
    row = await session.get(Source, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="source not found")
    row.paused = True
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return {"status": "paused"}


@router.post("/{source_id}/resume")
async def resume_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("sources:write")),
) -> dict[str, str]:
    row = await session.get(Source, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="source not found")
    row.paused = False
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return {"status": "active"}


def _to_descriptor(row: Source) -> SourceDescriptor:
    return SourceDescriptor.model_validate(
        {
            "source_id": row.source_id,
            "tenant": row.tenant,
            "app": row.app,
            "name": row.name,
            "cadence": row.cadence,
            "modality": row.modality,
            "mime_hint": row.mime_hint,
            "embed_with": row.embed_with,
            "partition_by": row.partition_by,
            "dedup_keys": row.dedup_keys,
            "policy": row.policy,
            "pull_config": row.pull_config,
            "paused": row.paused,
            "created_at": row.created_at,
        }
    )
