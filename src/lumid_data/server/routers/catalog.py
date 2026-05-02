"""Dataset catalog read-side endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import Dataset
from ..auth.security import PrincipalContext, require_scope
from ..deps import get_session

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])


@router.get("")
async def list_datasets(
    source_id: str | None = None,
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("datasets:read")),
) -> list[dict[str, Any]]:
    stmt = select(Dataset)
    if source_id:
        stmt = stmt.where(Dataset.source_id == source_id)
    stmt = stmt.order_by(Dataset.updated_at.desc()).limit(200)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    session: AsyncSession = Depends(get_session),
    _principal: PrincipalContext = Depends(require_scope("datasets:read")),
) -> dict[str, Any]:
    row = await session.get(Dataset, dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return _serialize(row)


def _serialize(row: Dataset) -> dict[str, Any]:
    return {
        "dataset_id": row.dataset_id,
        "source_id": row.source_id,
        "table_uri": row.table_uri,
        "volume_uri": row.volume_uri,
        "topic": row.topic,
        "modality": row.modality,
        "schema_fp": row.schema_fp,
        "version": row.version,
        "rows_total": row.rows_total,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
