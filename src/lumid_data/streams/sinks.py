"""Stream sinks: PostgresTable (with optional hypertable) and S3Object.

A sink descriptor is a plain dict pulled from ``stream_sources.sink``.
Routing happens via ``write(sink, msg, ctx)``: switch on ``sink['kind']``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from .base import StreamMessage

logger = logging.getLogger("lumid_data.streams.sinks")

_VALID_IDENT = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _quote_ident(ident: str) -> str:
    if not ident or not all(c in _VALID_IDENT for c in ident):
        raise ValueError(f"unsafe identifier: {ident!r}")
    return f'"{ident}"'


@dataclass
class SinkContext:
    """Per-write context: SQLAlchemy engine for /db sinks, boto3 client for /storage."""

    engine: AsyncEngine | None = None
    s3_client: Any | None = None


async def write(sink: dict[str, Any], msg: StreamMessage, ctx: SinkContext) -> None:
    kind = sink.get("kind", "postgres_table")
    if kind == "postgres_table":
        if ctx.engine is None:
            raise RuntimeError("postgres_table sink needs engine")
        await _write_postgres(sink, msg, ctx.engine)
        return
    if kind == "s3_object":
        if ctx.s3_client is None:
            raise RuntimeError("s3_object sink needs s3_client")
        _write_s3(sink, msg, ctx.s3_client)
        return
    raise ValueError(f"unknown sink kind: {kind!r}")


async def ensure_postgres_landing(sink: dict[str, Any], engine: AsyncEngine) -> None:
    """Create the landing table (and hypertable if requested) once at boot.

    Schema: (received_at TIMESTAMPTZ, payload JSONB, headers JSONB,
    offset JSONB). Caller is expected to project columns via materialized
    views or PostgREST RPC; we keep the core table tiny.
    """
    schema = _quote_ident(sink.get("schema", "public"))
    table = _quote_ident(sink["table"])
    fq = f"{schema}.{table}"
    create = (  # nosec B608 — schema/table go through _quote_ident
        f"CREATE TABLE IF NOT EXISTS {fq} ("
        " received_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " payload JSONB NOT NULL,"
        " headers JSONB NOT NULL DEFAULT '{}'::jsonb,"
        " stream_offset JSONB NOT NULL DEFAULT '{}'::jsonb"
        ");"
    )
    async with engine.begin() as conn:
        await conn.execute(text(create))
        if sink.get("hypertable"):
            time_col = sink.get("time_column", "received_at")
            if time_col not in {"received_at"} and time_col not in sink.get(
                "extra_columns", []
            ):
                raise ValueError(
                    "hypertable time_column must be 'received_at' or in extra_columns"
                )
            await conn.execute(
                text(
                    "SELECT create_hypertable(:fq, :col, if_not_exists => TRUE);"
                ).bindparams(fq=fq, col=time_col)
            )


async def _write_postgres(
    sink: dict[str, Any], msg: StreamMessage, engine: AsyncEngine
) -> None:
    schema = _quote_ident(sink.get("schema", "public"))
    table = _quote_ident(sink["table"])
    sql_head = f"INSERT INTO {schema}.{table} (payload, headers, stream_offset) "  # nosec B608 — _quote_ident whitelists identifiers
    sql_tail = "VALUES (:payload, :headers, :offset)"
    stmt = text(sql_head + sql_tail).bindparams(
        payload=json.dumps(msg.payload),
        headers=json.dumps(msg.headers),
        offset=json.dumps(msg.offset),
    )
    async with engine.begin() as conn:
        await conn.execute(stmt)


def _write_s3(sink: dict[str, Any], msg: StreamMessage, client: Any) -> None:
    bucket = sink["bucket"]
    prefix = sink.get("prefix", "").rstrip("/")
    now = datetime.now(UTC)
    ts = int(now.timestamp() * 1000)
    tag = _offset_tag(msg)
    base = f"{now:%Y/%m/%d}/{ts}-{tag}.jsonl"
    key = f"{prefix}/{base}" if prefix else base
    body = json.dumps(
        {
            "received_at": now.isoformat(),
            "payload": msg.payload,
            "headers": msg.headers,
            "offset": msg.offset,
        }
    )
    client.put_object(Bucket=bucket, Key=key, Body=body.encode())


def _offset_tag(msg: StreamMessage) -> str:
    if not msg.offset:
        return "0"
    parts = []
    for k in ("topic", "partition", "offset", "seq"):
        if k in msg.offset:
            parts.append(str(msg.offset[k]))
    return "-".join(parts) if parts else "x"


async def write_dlq(
    session: AsyncSession,
    source_id: str,
    msg: StreamMessage,
    error: str,
) -> None:
    from ..db.models import StreamDlq
    from ..utils.ids import new_dlq_id

    row = StreamDlq(
        id=new_dlq_id(),
        source_id=source_id,
        payload=msg.payload,
        headers=msg.headers,
        error=error,
    )
    session.add(row)
    await session.commit()
