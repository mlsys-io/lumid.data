"""Admin endpoints: DLQ list / replay."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import DlqEntry
from ..auth.security import PrincipalContext, require_scope
from ..deps import get_session

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get("/dlq")
async def list_dlq(
    source_id: str | None = None,
    replayed: bool | None = None,
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("admin:dlq")),
) -> list[dict[str, Any]]:
    stmt = select(DlqEntry)
    if source_id:
        stmt = stmt.where(DlqEntry.source_id == source_id)
    if replayed is not None:
        stmt = stmt.where(DlqEntry.replayed == replayed)
    stmt = stmt.order_by(DlqEntry.received_at.desc()).limit(200)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("/dlq/{dlq_id}/mark-replayed")
async def mark_replayed(
    dlq_id: str,
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("admin:dlq")),
) -> dict[str, str]:
    row = await session.get(DlqEntry, dlq_id)
    if row is None:
        raise HTTPException(status_code=404, detail="dlq entry not found")
    row.replayed = True
    await session.commit()
    return {"status": "marked"}


def _serialize(row: DlqEntry) -> dict[str, Any]:
    return {
        "dlq_id": row.dlq_id,
        "ingest_id": row.ingest_id,
        "source_id": row.source_id,
        "received_at": row.received_at.isoformat(),
        "reason": row.reason,
        "payload_uri": row.payload_uri,
        "quality_report": row.quality_report,
        "replayed": row.replayed,
    }
