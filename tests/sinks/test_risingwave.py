"""Unit tests for the RisingWave admin SQL generation.

We assert the exact DDL emitted (no live RW required); psycopg.connect
is patched to capture statements.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from lumid_data.sinks.risingwave import (
    RisingWaveConfig,
    StreamPipelineSpec,
    _ident,
    drop_stream_pipeline,
    ensure_stream_pipeline,
)


@contextmanager
def _fake_conn(existing_kinds: set[str]):
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn.cursor.return_value = cur
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    executed: list[str] = []

    def fake_execute(sql, *args, **kwargs):
        executed.append(sql)
        if "rw_sources" in sql:
            row = ("source",) if "source" in existing_kinds else None
            cur.fetchone.return_value = row
        elif "rw_materialized_views" in sql:
            row = ("mv",) if "mv" in existing_kinds else None
            cur.fetchone.return_value = row
        elif "rw_sinks" in sql:
            row = ("sink",) if "sink" in existing_kinds else None
            cur.fetchone.return_value = row

    cur.execute.side_effect = fake_execute
    yield conn, executed


@pytest.fixture
def spec() -> StreamPipelineSpec:
    return StreamPipelineSpec(
        pipeline_id="abc123",
        topic="lumid_data__acme__news",
        redpanda_brokers="redpanda:9092",
        delta_location="s3://lumid-data/datasets/lumid_data/acme/news",
        s3_endpoint="http://minio:9000",
        s3_access_key="lumid_data",
        s3_secret_key="lumid_data_secret",
    )


def test_ident_rejects_unsafe() -> None:
    with pytest.raises(ValueError):
        _ident("foo;DROP TABLE x;")


def test_ensure_creates_full_pipeline_when_nothing_exists(spec) -> None:
    with _fake_conn(existing_kinds=set()) as (conn, executed):
        with patch("psycopg.connect", return_value=conn):
            ensure_stream_pipeline(RisingWaveConfig(dsn="postgresql://rw"), spec)
    create_dml = [s for s in executed if s.startswith("CREATE")]
    assert any("CREATE SOURCE" in s for s in create_dml)
    assert any("CREATE MATERIALIZED VIEW" in s for s in create_dml)
    assert any("CREATE SINK" in s and "deltalake" in s for s in create_dml)
    assert any(spec.delta_location in s for s in create_dml)
    assert any(spec.topic in s for s in create_dml)


def test_ensure_is_idempotent_when_all_exist(spec) -> None:
    with _fake_conn(existing_kinds={"source", "mv", "sink"}) as (conn, executed):
        with patch("psycopg.connect", return_value=conn):
            ensure_stream_pipeline(RisingWaveConfig(dsn="postgresql://rw"), spec)
    assert not any(s.startswith("CREATE") for s in executed)


def test_drop_emits_three_drops() -> None:
    with _fake_conn(existing_kinds={"source", "mv", "sink"}) as (conn, executed):
        with patch("psycopg.connect", return_value=conn):
            drop_stream_pipeline(RisingWaveConfig(dsn="postgresql://rw"), "abc123")
    assert any("DROP SINK" in s for s in executed)
    assert any("DROP MATERIALIZED VIEW" in s for s in executed)
    assert any("DROP SOURCE" in s for s in executed)
