"""TimescaleDB sink unit tests."""

from unittest.mock import MagicMock, patch

import pytest

from lumid_data.sinks.timescale import (
    TimescaleSinkConfig,
    _qualified,
    ensure_hypertable,
    insert,
)


def test_qualified_splits_namespace() -> None:
    assert _qualified("lumid_data.acme.history") == ("lumid_data_acme", "history")


def test_qualified_rejects_unqualified() -> None:
    with pytest.raises(ValueError):
        _qualified("history")


def _conn():
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn.cursor.return_value = cur
    return conn, cur


def test_ensure_hypertable_runs_create_and_create_hypertable() -> None:
    conn, cur = _conn()
    with patch("psycopg.connect", return_value=conn):
        ensure_hypertable(
            TimescaleSinkConfig(dsn="postgresql://t"),
            table="lumid_data.acme.history",
            columns={"ts": "TIMESTAMPTZ NOT NULL", "v": "DOUBLE PRECISION"},
        )
    sqls = [c.args[0] for c in cur.execute.call_args_list]
    assert any("CREATE SCHEMA IF NOT EXISTS" in s for s in sqls)
    assert any("CREATE TABLE IF NOT EXISTS" in s for s in sqls)
    assert any("create_hypertable" in s for s in sqls)


def test_insert_executes_executemany() -> None:
    conn, cur = _conn()
    with patch("psycopg.connect", return_value=conn):
        n = insert(
            TimescaleSinkConfig(dsn="postgresql://t"),
            table="lumid_data.acme.history",
            columns=["ts", "v"],
            rows=[("2026-05-02T00:00:00Z", 1.0), ("2026-05-02T00:00:01Z", 2.0)],
        )
    assert n == 2
    cur.executemany.assert_called_once()


def test_ensure_hypertable_requires_time_column_in_columns() -> None:
    with pytest.raises(ValueError):
        ensure_hypertable(
            TimescaleSinkConfig(dsn="postgresql://t"),
            table="lumid_data.acme.history",
            columns={"v": "DOUBLE PRECISION"},
        )
