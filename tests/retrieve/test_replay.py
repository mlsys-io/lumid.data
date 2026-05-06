"""Tests for the deterministic plan replayer.

psycopg + boto3 are mocked. The replayer's job is to dispatch on op type,
write rows / bytes correctly per output format, and surface errors as
``ReplayError`` so the router can retry once.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from lumid_data.sdk.schemas import (
    RetrievalPlan,
    RetrievalSqlOp,
    RetrievalStorageGetOp,
)

from lumid_data.retrieve import replay as replay_module
from lumid_data.retrieve.replay import PlanReplayer, ReplayError


@pytest.fixture
def fake_s3() -> MagicMock:
    s3 = MagicMock()

    class _Body:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def iter_chunks(self, chunk_size: int = 64 * 1024):
            yield self._data

    s3.get_object.side_effect = lambda **kw: {"Body": _Body(b"hello world\n")}
    return s3


@pytest.fixture
def fake_psycopg(monkeypatch) -> MagicMock:
    """Patch ``psycopg.AsyncConnection.connect`` to return a scripted cursor."""
    cur = MagicMock()
    cur.execute = AsyncMock(return_value=None)
    cur.description = [("symbol",), ("close",)]
    cur.fetchall = AsyncMock(
        return_value=[
            {"symbol": "NVDA", "close": 100.5},
            {"symbol": "NVDA", "close": 101.5},
        ]
    )

    @asynccontextmanager
    async def cursor_cm(*args, **kwargs):
        yield cur

    @asynccontextmanager
    async def conn_cm(*args, **kwargs):
        conn = MagicMock()
        conn.cursor = cursor_cm
        yield conn

    async def connect(*args, **kwargs):
        return conn_cm(*args, **kwargs)

    monkeypatch.setattr(
        replay_module.psycopg.AsyncConnection,
        "connect",
        AsyncMock(side_effect=connect),
    )
    return cur


class TestReplaySql:
    @pytest.mark.asyncio
    async def test_jsonl_writes_one_row_per_line(
        self, tmp_path: Path, fake_s3: MagicMock, fake_psycopg: MagicMock
    ):
        replayer = PlanReplayer(
            database_url="postgresql://x:y@host/db",
            s3_client=fake_s3,
            admin_role="postgres",
        )
        plan = RetrievalPlan(
            plan=[RetrievalSqlOp(op="sql", query="SELECT * FROM t")],
        )
        out = tmp_path / "result.jsonl"
        result = await replayer.replay(plan=plan, out_path=out, output_format="jsonl")
        lines = out.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["symbol"] == "NVDA"
        assert result.rowcount == 2
        assert result.size_bytes == out.stat().st_size
        assert len(result.access_chain) == 1
        assert result.access_chain[0].op == "sql"
        assert result.access_chain[0].rows_or_bytes == 2

    @pytest.mark.asyncio
    async def test_csv_writes_header_and_rows(
        self, tmp_path: Path, fake_s3: MagicMock, fake_psycopg: MagicMock
    ):
        replayer = PlanReplayer(
            database_url="postgresql://x:y@host/db",
            s3_client=fake_s3,
            admin_role="postgres",
        )
        plan = RetrievalPlan(
            plan=[RetrievalSqlOp(op="sql", query="SELECT * FROM t")],
        )
        out = tmp_path / "result.csv"
        result = await replayer.replay(plan=plan, out_path=out, output_format="csv")
        rows = out.read_text().splitlines()
        assert rows[0] == "symbol,close"
        assert "NVDA" in rows[1]
        assert result.rowcount == 2

    @pytest.mark.asyncio
    async def test_strips_trailing_semicolons(
        self, tmp_path: Path, fake_s3: MagicMock, fake_psycopg: MagicMock
    ):
        replayer = PlanReplayer(
            database_url="postgresql://x:y@host/db",
            s3_client=fake_s3,
            admin_role="postgres",
        )
        plan = RetrievalPlan(
            plan=[RetrievalSqlOp(op="sql", query="SELECT * FROM t;")],
        )
        out = tmp_path / "result.jsonl"
        await replayer.replay(plan=plan, out_path=out, output_format="jsonl")
        # The query passed to cur.execute should be stripped of the trailing ;
        executed_sql = fake_psycopg.execute.await_args_list[1].args[0]
        assert not executed_sql.endswith(";")


class TestReplayStorage:
    @pytest.mark.asyncio
    async def test_raw_writes_object_bytes_verbatim(
        self, tmp_path: Path, fake_s3: MagicMock, fake_psycopg: MagicMock
    ):
        replayer = PlanReplayer(
            database_url="postgresql://x:y@host/db",
            s3_client=fake_s3,
            admin_role="postgres",
        )
        plan = RetrievalPlan(
            plan=[RetrievalStorageGetOp(op="storage_get", bucket="b", key="k.txt")],
        )
        out = tmp_path / "blob.bin"
        result = await replayer.replay(plan=plan, out_path=out, output_format="raw")
        assert out.read_bytes() == b"hello world\n"
        assert result.rowcount == 1
        assert result.size_bytes == len(b"hello world\n")
        assert result.access_chain[0].op == "storage_get"

    @pytest.mark.asyncio
    async def test_raw_rejects_multi_op_plan(
        self, tmp_path: Path, fake_s3: MagicMock, fake_psycopg: MagicMock
    ):
        replayer = PlanReplayer(
            database_url="postgresql://x:y@host/db",
            s3_client=fake_s3,
            admin_role="postgres",
        )
        plan = RetrievalPlan(
            plan=[
                RetrievalStorageGetOp(op="storage_get", bucket="b", key="a"),
                RetrievalStorageGetOp(op="storage_get", bucket="b", key="b"),
            ],
        )
        out = tmp_path / "blob.bin"
        with pytest.raises(ReplayError, match="raw output_format"):
            await replayer.replay(plan=plan, out_path=out, output_format="raw")


class TestReplayMixed:
    @pytest.mark.asyncio
    async def test_jsonl_mixes_sql_rows_and_storage_records(
        self, tmp_path: Path, fake_s3: MagicMock, fake_psycopg: MagicMock
    ):
        replayer = PlanReplayer(
            database_url="postgresql://x:y@host/db",
            s3_client=fake_s3,
            admin_role="postgres",
        )
        plan = RetrievalPlan(
            plan=[
                RetrievalSqlOp(op="sql", query="SELECT * FROM t"),
                RetrievalStorageGetOp(op="storage_get", bucket="b", key="k.txt"),
            ],
        )
        out = tmp_path / "result.jsonl"
        result = await replayer.replay(plan=plan, out_path=out, output_format="jsonl")
        records = [json.loads(line) for line in out.read_text().splitlines()]
        assert len(records) == 3  # 2 sql rows + 1 storage record
        # The storage record carries op + bucket + key + size + sha256
        storage_rec = records[2]
        assert storage_rec["op"] == "storage_get"
        assert storage_rec["bucket"] == "b"
        assert storage_rec["key"] == "k.txt"
        assert storage_rec["size"] == len(b"hello world\n")
        assert "sha256" in storage_rec
        assert len(result.access_chain) == 2


class TestReplayErrors:
    @pytest.mark.asyncio
    async def test_empty_plan_raises(
        self, tmp_path: Path, fake_s3: MagicMock, fake_psycopg: MagicMock
    ):
        replayer = PlanReplayer(
            database_url="postgresql://x:y@host/db",
            s3_client=fake_s3,
            admin_role="postgres",
        )
        plan = RetrievalPlan(plan=[])
        out = tmp_path / "result.jsonl"
        with pytest.raises(ReplayError, match="no ops"):
            await replayer.replay(plan=plan, out_path=out, output_format="jsonl")
