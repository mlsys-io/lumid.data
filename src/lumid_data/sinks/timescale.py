"""TimescaleDB sink for time-series modalities.

A hypertable per dataset (``lumid_data.<tenant>.<app>.<name>`` mapped to a
schema-namespaced table). The descriptor's ``partition_by[0]`` is the
time column (defaults to ``ts``). Inserts go via psycopg COPY for
throughput; small batches use INSERT.
"""

import logging
from dataclasses import dataclass
from typing import Any

import psycopg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimescaleSinkConfig:
    dsn: str  # postgresql://user:pass@host:5432/db


def _qualified(table: str) -> tuple[str, str]:
    """Map ``lumid_data.tenant.app.name`` -> (schema, table)."""
    parts = table.split(".")
    if len(parts) < 2:
        raise ValueError(f"timescale.table must be schema.name (got {table!r})")
    schema = "_".join(parts[:-1])
    name = parts[-1]
    return schema, name


def ensure_hypertable(
    cfg: TimescaleSinkConfig,
    table: str,
    columns: dict[str, str],
    time_column: str = "ts",
) -> None:
    schema, name = _qualified(table)
    if time_column not in columns:
        raise ValueError(f"time_column {time_column!r} missing from columns")

    cols_sql = ", ".join(f'"{c}" {t}' for c, t in columns.items())
    with psycopg.connect(cfg.dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cur.execute(f'CREATE TABLE IF NOT EXISTS "{schema}"."{name}" ({cols_sql})')
        cur.execute(
            "SELECT create_hypertable(%s, %s, if_not_exists => TRUE)",
            (f"{schema}.{name}", time_column),
        )
        logger.info("ensured hypertable %s.%s on %s", schema, name, time_column)


def insert(
    cfg: TimescaleSinkConfig,
    table: str,
    columns: list[str],
    rows: list[tuple[Any, ...]],
) -> int:
    if not rows:
        return 0
    schema, name = _qualified(table)
    cols_quoted = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f'INSERT INTO "{schema}"."{name}" ({cols_quoted}) VALUES ({placeholders})'
    with psycopg.connect(cfg.dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)
