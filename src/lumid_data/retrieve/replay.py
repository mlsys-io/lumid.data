"""Deterministic plan executor.

Takes a parsed :class:`RetrievalPlan` and runs each op directly against
the same psycopg + boto3 surfaces that ``/sql/v1`` and ``/storage/v1``
use — no agent in the loop. Streams rows / object bytes into a single
output file under ``out_path``.

Output format choice:
- ``csv`` / ``jsonl`` — for plans with one or more SQL ops; rows from each
  op are concatenated in order. ``csv`` writes the union of all column
  names from the first op; subsequent ops with different schemas trigger
  a fall-through to JSONL.
- ``raw`` — for plans where the only op is a single ``storage_get``;
  the object's bytes are written verbatim. With multiple storage ops
  or mixed plans the replayer concatenates JSONL records describing
  each fetched blob (op, bucket, key, size, sha256).
"""

import csv
import hashlib
import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import psycopg
from lumid_data.sdk.schemas import (
    AccessStep,
    RetrievalPlan,
    RetrievalSqlOp,
    RetrievalStorageGetOp,
)
from psycopg.rows import dict_row
from sqlalchemy.engine.url import make_url

logger = logging.getLogger(__name__)


@dataclass
class ReplayResult:
    out_path: Path
    rowcount: int
    size_bytes: int
    access_chain: list[AccessStep]
    elapsed_ms: int


class _RowWriter:
    def write_row(self, row: dict[str, Any]) -> None:
        raise NotImplementedError


class _JsonlWriter(_RowWriter):
    def __init__(self, out: IO[str]) -> None:
        self._out = out

    def write_row(self, row: dict[str, Any]) -> None:
        self._out.write(json.dumps(row, default=str) + "\n")


class _CsvLazyWriter(_RowWriter):
    """CSV writer that infers the column set from the first row written."""

    def __init__(self, out: IO[str]) -> None:
        self._out = out
        self._writer: csv.DictWriter[str] | None = None
        self._fieldnames: list[str] | None = None

    def write_row(self, row: dict[str, Any]) -> None:
        if self._writer is None:
            self._fieldnames = list(row.keys())
            self._writer = csv.DictWriter(
                self._out, fieldnames=self._fieldnames, extrasaction="ignore"
            )
            self._writer.writeheader()
        if set(row.keys()) - set(self._fieldnames or []):
            extra = set(row.keys()) - set(self._fieldnames or [])
            logger.warning(
                "csv row has extra keys %s not in header; falling back to jsonl",
                extra,
            )
        self._writer.writerow(
            {k: _stringify(v) for k, v in row.items() if k in (self._fieldnames or [])}
        )


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    return json.dumps(v, default=str)


class PlanReplayer:
    """Deterministic replay over psycopg + boto3."""

    def __init__(
        self,
        *,
        database_url: str,
        s3_client: Any,
        admin_role: str,
    ) -> None:
        self._database_url = database_url
        self._s3 = s3_client
        self._admin_role = admin_role

    async def replay(
        self,
        *,
        plan: RetrievalPlan,
        out_path: Path,
        output_format: str,
    ) -> ReplayResult:
        if not plan.plan:
            raise ReplayError("plan has no ops to execute")
        started = time.monotonic()
        access: list[AccessStep] = []
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if output_format == "raw":
            rowcount, size_bytes = await self._replay_raw(plan, out_path, access)
        else:
            rowcount, size_bytes = await self._replay_text(
                plan, out_path, output_format, access
            )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ReplayResult(
            out_path=out_path,
            rowcount=rowcount,
            size_bytes=size_bytes,
            access_chain=access,
            elapsed_ms=elapsed_ms,
        )

    async def _replay_text(
        self,
        plan: RetrievalPlan,
        out_path: Path,
        output_format: str,
        access: list[AccessStep],
    ) -> tuple[int, int]:
        rowcount = 0
        with out_path.open("w", newline="") as out:
            writer: _RowWriter = (
                _CsvLazyWriter(out) if output_format == "csv" else _JsonlWriter(out)
            )
            async with await psycopg.AsyncConnection.connect(
                _libpq_dsn(self._database_url), autocommit=True
            ) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(f"SET LOCAL ROLE {self._admin_role}")
                    for op in plan.plan:
                        try:
                            if isinstance(op, RetrievalSqlOp):
                                n = await self._run_sql_op(cur, op, writer)
                                access.append(
                                    AccessStep(
                                        op="sql",
                                        query=op.query,
                                        rows_or_bytes=n,
                                    )
                                )
                                rowcount += n
                            elif isinstance(op, RetrievalStorageGetOp):
                                n = self._run_storage_op_into_jsonl(op, writer)
                                access.append(
                                    AccessStep(
                                        op="storage_get",
                                        bucket=op.bucket,
                                        key=op.key,
                                        rows_or_bytes=n,
                                    )
                                )
                                rowcount += 1
                        except ReplayError:
                            raise
                        except Exception as exc:
                            kind = (
                                "sql"
                                if isinstance(op, RetrievalSqlOp)
                                else "storage_get"
                            )
                            raise ReplayError(f"{kind} op failed: {exc}") from exc
        size_bytes = out_path.stat().st_size
        return rowcount, size_bytes

    async def _replay_raw(
        self,
        plan: RetrievalPlan,
        out_path: Path,
        access: list[AccessStep],
    ) -> tuple[int, int]:
        if not (
            len(plan.plan) == 1 and isinstance(plan.plan[0], RetrievalStorageGetOp)
        ):
            raise ReplayError(
                "raw output_format requires a single storage_get op; got "
                f"{len(plan.plan)} ops or non-storage op"
            )
        op = plan.plan[0]
        size_bytes = 0
        try:
            with out_path.open("wb") as out:
                for chunk in self._stream_object(op.bucket, op.key):
                    out.write(chunk)
                    size_bytes += len(chunk)
        except Exception as exc:
            raise ReplayError(f"storage_get op failed: {exc}") from exc
        access.append(
            AccessStep(
                op="storage_get",
                bucket=op.bucket,
                key=op.key,
                rows_or_bytes=size_bytes,
            )
        )
        return 1, size_bytes

    async def _run_sql_op(
        self,
        cur: Any,
        op: RetrievalSqlOp,
        writer: _RowWriter,
    ) -> int:
        cleaned = op.query.rstrip().rstrip(";").rstrip()
        await cur.execute(cleaned)
        if cur.description is None:
            return 0
        rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            writer.write_row(row)
        return len(rows)

    def _run_storage_op_into_jsonl(
        self, op: RetrievalStorageGetOp, writer: _RowWriter
    ) -> int:
        """When the plan mixes storage_get with other ops, write a record per blob."""
        body = b"".join(self._stream_object(op.bucket, op.key))
        sha = hashlib.sha256(body).hexdigest()
        record = {
            "op": "storage_get",
            "bucket": op.bucket,
            "key": op.key,
            "size": len(body),
            "sha256": sha,
        }
        writer.write_row(record)
        return len(body)

    def _stream_object(self, bucket: str, key: str) -> Iterator[bytes]:
        obj = self._s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"]
        yield from body.iter_chunks(chunk_size=64 * 1024)


class ReplayError(RuntimeError):
    """Raised on plan-execution failure (SQL error, missing key, etc.)."""


def _libpq_dsn(url: str) -> str:
    parsed = make_url(url)
    if "+" in parsed.drivername:
        parsed = parsed.set(drivername=parsed.drivername.split("+", 1)[0])
    return parsed.render_as_string(hide_password=False)
