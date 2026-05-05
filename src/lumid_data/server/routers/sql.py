"""``POST /sql/v1`` — psycopg passthrough running as the admin role.

With no auth configured, every request runs under the admin Postgres role.
A registered `IdentityProvider` plugin can bind a real principal; downstream
overlays are free to add their own role-resolution shim on top.
Multi-statement strings are rejected (one query per call).
"""

import logging

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from lumid_data.sdk.schemas import SqlResult
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from sqlalchemy.engine.url import make_url

from ..auth.security import default_principal
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


def _libpq_dsn(url: str) -> str:
    """Strip the SQLAlchemy driver suffix so raw psycopg accepts the DSN."""
    parsed = make_url(url)
    if "+" in parsed.drivername:
        parsed = parsed.set(drivername=parsed.drivername.split("+", 1)[0])
    return parsed.render_as_string(hide_password=False)


@router.post("", response_model=SqlResult)
async def run_sql(
    body: SqlRequest,
    state: AppState = Depends(get_state),
) -> SqlResult:
    _validate_single_statement(body.query)
    principal = default_principal()

    role = state.settings.postgrest_admin_role
    started = now_ms()
    rows: list[dict] = []
    rowcount = 0
    error: str | None = None
    status_code = 200
    try:
        async with await psycopg.AsyncConnection.connect(
            _libpq_dsn(state.settings.database_url), autocommit=True
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
    return SqlResult(rows=rows, rowcount=rowcount)
