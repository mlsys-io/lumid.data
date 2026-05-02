"""Postgres CDC pull connector (stub for Phase 3 first cut).

Captures inserted rows from a table since the last seen primary-key
cursor. Real CDC (logical replication slots, WAL streaming) is a Phase 4
follow-up — this first cut is poll-based, which is sufficient for
research datasets that are append-only or change rarely.
"""

import logging
from dataclasses import dataclass
from typing import Any

import psycopg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CdcPgSpec:
    dsn: str
    table: str  # schema.table
    pk_column: str = "id"


def fetch_since(
    spec: CdcPgSpec,
    cursor: int | str | None,
    limit: int = 1000,
) -> tuple[list[dict[str, Any]], int | str | None]:
    """Return (rows, new_cursor) for rows where pk > cursor."""
    where = "WHERE 1=1"
    params: tuple[Any, ...] = ()
    if cursor is not None:
        where = f'WHERE "{spec.pk_column}" > %s'
        params = (cursor,)
    sql = (
        f"SELECT * FROM {spec.table} {where} "
        f'ORDER BY "{spec.pk_column}" ASC LIMIT {int(limit)}'
    )
    rows: list[dict[str, Any]] = []
    new_cursor: int | str | None = cursor
    with (
        psycopg.connect(spec.dsn) as conn,
        conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
    ):
        cur.execute(sql, params)
        for row in cur.fetchall():
            rows.append(row)
            if spec.pk_column in row:
                new_cursor = row[spec.pk_column]
    return rows, new_cursor
