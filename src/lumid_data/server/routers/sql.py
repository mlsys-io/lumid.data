"""``/sql/v1`` — psycopg passthrough running as the admin role.

Every request runs under the admin Postgres role. A registered
`IdentityProvider` plugin can bind a real principal if a deployment
needs role-scoped queries; the binding lives in the plugin, not here.
Multi-statement strings are rejected (one query per call).

Probe routes (``/explain``, ``/count``, ``/sample``) wrap the same
psycopg passthrough but cap what the caller can observe — used by
the agent during NL2SQL planning so the planner sees schema/shape
without pulling full result sets into its context.
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


class SqlSampleRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200_000)
    params: list = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)


def _validate_single_statement(sql: str) -> None:
    cleaned = " ".join(sql.split())
    if cleaned.count(";") > 1 or (
        cleaned.count(";") == 1 and not cleaned.endswith(";")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="multi-statement queries are not allowed",
        )


def _strip_trailing_semicolon(sql: str) -> str:
    return sql.rstrip().rstrip(";").rstrip()


def _libpq_dsn(url: str) -> str:
    """Strip the SQLAlchemy driver suffix so raw psycopg accepts the DSN."""
    parsed = make_url(url)
    if "+" in parsed.drivername:
        parsed = parsed.set(drivername=parsed.drivername.split("+", 1)[0])
    return parsed.render_as_string(hide_password=False)


async def _execute(
    state: AppState, query: str, params: list, op: str, path: str
) -> SqlResult:
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
                await cur.execute(query, params)
                rowcount = cur.rowcount
                if cur.description is not None:
                    rows = [dict(r) for r in await cur.fetchall()]
    except Exception as exc:
        error = str(exc)
        status_code = 400
        logger.warning("sql failed (%s): %s", op, exc)
    elapsed = now_ms() - started
    await state.audit.record(
        principal=principal,
        surface="sql",
        op=op,
        path=path,
        status_code=status_code,
        latency_ms=elapsed,
        error=error,
        request_meta={"role": role, "rows": len(rows), "rowcount": rowcount},
    )
    if error is not None:
        raise HTTPException(status_code=status_code, detail=error)
    return SqlResult(rows=rows, rowcount=rowcount)


@router.post("", response_model=SqlResult)
async def run_sql(
    body: SqlRequest,
    state: AppState = Depends(get_state),
) -> SqlResult:
    """Run a single SQL statement and return all result rows."""
    _validate_single_statement(body.query)
    return await _execute(state, body.query, body.params, "QUERY", "/sql/v1")


@router.post("/explain", response_model=SqlResult)
async def run_sql_explain(
    body: SqlRequest,
    state: AppState = Depends(get_state),
) -> SqlResult:
    """Return the EXPLAIN plan for the given SELECT query.

    Used during agent NL2SQL planning to inspect query shape without
    materializing rows. The query is wrapped with ``EXPLAIN`` server-side.
    """
    _validate_single_statement(body.query)
    inner = _strip_trailing_semicolon(body.query)
    wrapped = f"EXPLAIN {inner}"
    return await _execute(state, wrapped, body.params, "EXPLAIN", "/sql/v1/explain")


@router.post("/count", response_model=SqlResult)
async def run_sql_count(
    body: SqlRequest,
    state: AppState = Depends(get_state),
) -> SqlResult:
    """Return the row count for the given SELECT query.

    The query is wrapped server-side as ``SELECT COUNT(*) FROM (<q>) sub``;
    a single row with a single ``count`` column is returned. Used during
    agent planning to validate shape without pulling full data.
    """
    _validate_single_statement(body.query)
    inner = _strip_trailing_semicolon(body.query)
    wrapped = f"SELECT COUNT(*) AS count FROM ({inner}) AS sub"  # nosec B608 — inner is already validated as a single statement; wrapping inside a subselect is the explicit goal of this probe route
    return await _execute(state, wrapped, body.params, "COUNT", "/sql/v1/count")


@router.post("/sample", response_model=SqlResult)
async def run_sql_sample(
    body: SqlSampleRequest,
    state: AppState = Depends(get_state),
) -> SqlResult:
    """Return at most ``limit`` rows (≤20) of the given SELECT query.

    Wrapped server-side as ``SELECT * FROM (<q>) sub LIMIT N`` so an inner
    LIMIT cannot raise the cap. Used during agent planning to inspect
    column names + a few rows without pulling full data.
    """
    _validate_single_statement(body.query)
    inner = _strip_trailing_semicolon(body.query)
    wrapped = f"SELECT * FROM ({inner}) AS sub LIMIT {body.limit}"  # nosec B608 — inner is already validated as a single statement; body.limit is a Pydantic int field clamped to ≤20
    return await _execute(state, wrapped, body.params, "SAMPLE", "/sql/v1/sample")
