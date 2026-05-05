"""``/sql/v1`` validation tests (mocked psycopg)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from lumid_data.server.routers.sql import SqlRequest, run_sql


class _Settings:
    postgrest_admin_role = "admin"
    postgrest_user_role = "user"
    postgrest_anon_role = "anon"
    database_url = "postgresql://host/db"


@pytest.mark.asyncio
async def test_run_sql_rejects_multi_statement() -> None:
    state = MagicMock()
    state.settings = _Settings()
    state.audit.record = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await run_sql(SqlRequest(query="SELECT 1; SELECT 2"), state=state)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_run_sql_returns_rows() -> None:
    state = MagicMock()
    state.settings = _Settings()
    state.audit.record = AsyncMock()

    fake_cur = MagicMock()
    fake_cur.execute = AsyncMock()
    fake_cur.fetchall = AsyncMock(return_value=[{"x": 1}])
    fake_cur.rowcount = 1
    fake_cur.description = [("x",)]
    fake_cur.__aenter__ = AsyncMock(return_value=fake_cur)
    fake_cur.__aexit__ = AsyncMock(return_value=False)

    fake_conn = MagicMock()
    fake_conn.cursor = MagicMock(return_value=fake_cur)
    fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_conn.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "psycopg.AsyncConnection.connect",
        AsyncMock(return_value=fake_conn),
    ):
        result = await run_sql(SqlRequest(query="SELECT 1 AS x"), state=state)
    assert result.rows == [{"x": 1}]
    assert result.rowcount == 1
