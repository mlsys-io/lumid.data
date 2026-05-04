"""Sinks: postgres SQL shape + S3 put_object + DLQ insert + identifier guard."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lumid_data.streams import sinks
from lumid_data.streams.base import StreamMessage


def test_quote_ident_rejects_unsafe() -> None:
    with pytest.raises(ValueError):
        sinks._quote_ident("ev'il")
    with pytest.raises(ValueError):
        sinks._quote_ident("")
    assert sinks._quote_ident("public") == '"public"'
    assert sinks._quote_ident("Tickers_2026") == '"Tickers_2026"'


def test_offset_tag_compact() -> None:
    msg = StreamMessage(payload={}, offset={"topic": "t", "partition": 0, "offset": 42})
    assert sinks._offset_tag(msg) == "t-0-42"
    assert sinks._offset_tag(StreamMessage(payload={})) == "0"


async def test_write_postgres_emits_insert() -> None:
    captured: dict[str, Any] = {}

    class _Conn:
        async def execute(self, stmt: Any) -> None:
            captured["sql"] = str(stmt)
            captured["params"] = stmt.compile().params

    class _Begin:
        async def __aenter__(self) -> _Conn:
            return _Conn()

        async def __aexit__(self, *_: Any) -> None:
            return None

    class _Engine:
        def begin(self) -> _Begin:
            return _Begin()

    sink = {"kind": "postgres_table", "schema": "public", "table": "ticks"}
    msg = StreamMessage(
        payload={"px": 100}, headers={"x-source": "demo"}, offset={"seq": 1}
    )
    await sinks._write_postgres(sink, msg, _Engine())  # type: ignore[arg-type]
    assert "INSERT INTO" in captured["sql"]
    assert json.loads(captured["params"]["payload"]) == {"px": 100}


def test_write_s3_uses_dated_key() -> None:
    client = MagicMock()
    sink = {"kind": "s3_object", "bucket": "lumid-data", "prefix": "streams/demo"}
    msg = StreamMessage(payload={"px": 100}, offset={"seq": 7})
    sinks._write_s3(sink, msg, client)
    args, kwargs = client.put_object.call_args
    assert kwargs["Bucket"] == "lumid-data"
    assert kwargs["Key"].startswith("streams/demo/")
    body = json.loads(kwargs["Body"])
    assert body["payload"] == {"px": 100}


async def test_write_dlq_persists_row() -> None:
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    msg = StreamMessage(payload={"x": 1}, headers={"h": "1"})
    await sinks.write_dlq(session, "str-abc", msg, "boom")
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
