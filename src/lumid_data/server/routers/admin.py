"""``/v1/admin/*`` — read views over the audit_log + agent_runs tables."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import AgentRun, AuditLog
from ..deps import get_session

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get("/audit")
async def list_audit(
    surface: str | None = None,
    op: str | None = None,
    principal_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    stmt = select(AuditLog).order_by(desc(AuditLog.received_at)).limit(limit)
    if surface:
        stmt = stmt.where(AuditLog.surface == surface)
    if op:
        stmt = stmt.where(AuditLog.op == op)
    if principal_id:
        stmt = stmt.where(AuditLog.principal_id == principal_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "received_at": r.received_at.isoformat(),
                "principal_id": r.principal_id,
                "org_id": r.org_id,
                "surface": r.surface,
                "op": r.op,
                "path": r.path,
                "status_code": r.status_code,
                "latency_ms": r.latency_ms,
                "error": r.error,
                "request_meta": r.request_meta,
            }
            for r in rows
        ]
    }


@router.get("/runs")
async def list_runs(
    principal_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    stmt = select(AgentRun).order_by(desc(AgentRun.received_at)).limit(limit)
    if principal_id:
        stmt = stmt.where(AgentRun.principal_id == principal_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "received_at": r.received_at.isoformat(),
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "principal_id": r.principal_id,
                "provider": r.provider,
                "model": r.model,
                "goal": r.goal,
                "status": r.status,
                "steps_taken": r.steps_taken,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "final_text": r.final_text,
                "error": r.error,
            }
            for r in rows
        ]
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await session.get(AgentRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="agent run not found")
    return {
        "id": row.id,
        "received_at": row.received_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "principal_id": row.principal_id,
        "provider": row.provider,
        "model": row.model,
        "goal": row.goal,
        "status": row.status,
        "steps_taken": row.steps_taken,
        "tokens_in": row.tokens_in,
        "tokens_out": row.tokens_out,
        "final_text": row.final_text,
        "transcript": row.transcript,
        "error": row.error,
    }
