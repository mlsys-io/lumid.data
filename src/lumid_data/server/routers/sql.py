"""``POST /sql/v1`` — psycopg passthrough scoped to the principal's role.

Reads run by default. DML requires the ``sql:write`` scope; the route
sets the session role to either the user role or admin role accordingly,
so RLS policies on the underlying tables apply.

Multi-statement strings are rejected (one query per call).
"""

import logging

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from ..auth.security import PrincipalContext, authenticate_bearer
from ..deps import get_state
from ..services.audit import now_ms
from ..state import AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sql/v1", tags=["sql"])


class SqlRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200_000)
    params: list = Field(default_factory=list)


def _validate_single_statement(sql: str) -> None:
    cleaned = " ".join(sql.split())
    if cleaned.count(";") > 1 or (
        cleaned.count(";") == 1 and not cleaned.endswith(";")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="multi-statement queries are not allowed",
        )


def _resolve_role(principal: PrincipalContext, settings) -> tuple[str, bool]:
    scopes = set(principal.scopes)
    if "*" in scopes:
        return settings.postgrest_admin_role, True
    if "sql:write" in scopes:
        return settings.postgrest_user_role, True
    if "sql:read" in scopes:
        return settings.postgrest_user_role, False
    return settings.postgrest_anon_role, False


@router.post("")
async def run_sql(
    body: SqlRequest,
    state: AppState = Depends(get_state),
    principal: PrincipalContext = Depends(authenticate_bearer),
) -> dict:
    if not (
        "*" in principal.scopes
        or "sql:read" in principal.scopes
        or "sql:write" in principal.scopes
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="sql:read or sql:write scope required",
        )
    _validate_single_statement(body.query)

    role, allow_writes = _resolve_role(principal, state.settings)
    started = now_ms()
    rows: list[dict] = []
    rowcount = 0
    error: str | None = None
    status_code = 200
    try:
        async with await psycopg.AsyncConnection.connect(
            state.settings.database_url, autocommit=allow_writes
        ) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(f"SET LOCAL ROLE {role}")
                await cur.execute(body.query, body.params)
                rowcount = cur.rowcount
                if cur.description is not None:
                    rows = [dict(r) for r in await cur.fetchall()]
    except Exception as exc:
        error = str(exc)
        status_code = 400
        logger.warning("sql failed: %s", exc)
    elapsed = now_ms() - started
    await state.audit.record(
        principal=principal,
        surface="sql",
        op="QUERY",
        path="/sql/v1",
        status_code=status_code,
        latency_ms=elapsed,
        error=error,
        request_meta={"role": role, "rows": len(rows), "rowcount": rowcount},
    )
    if error is not None:
        raise HTTPException(status_code=status_code, detail=error)
    return {"rows": rows, "rowcount": rowcount}
