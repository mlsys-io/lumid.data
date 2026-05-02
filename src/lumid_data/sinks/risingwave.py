"""RisingWave admin: bootstrap a Kafka source → MV → Delta sink pipeline.

RisingWave speaks the Postgres wire protocol; we drive it with
``psycopg``. The pipeline for a stream-cadence source is:

::

    CREATE SOURCE src_<id> (raw BYTEA)
      WITH (connector='kafka', topic='<topic>', ...)
      FORMAT PLAIN ENCODE BYTES;

    CREATE MATERIALIZED VIEW mv_<id> AS
      SELECT
        convert_from(raw, 'utf8')::JSONB AS payload,
        now() AS ts
      FROM src_<id>;

    CREATE SINK sink_<id> FROM mv_<id>
      WITH (
        connector='deltalake',
        s3.endpoint='...',
        s3.access.key='...',
        s3.secret.key='...',
        s3.region='...',
        location='s3://<bucket>/datasets/<table_path>'
      );

The `(payload, ts)` schema is intentionally minimal so we don't need a
schema declaration up-front. Custom-shape streams can override the MV
SQL via the descriptor's policy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RisingWaveConfig:
    dsn: str  # postgresql://user@host:4566/dev


@dataclass(frozen=True)
class StreamPipelineSpec:
    pipeline_id: str
    topic: str
    redpanda_brokers: str
    delta_location: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str = "us-east-1"
    mv_sql_override: str | None = None


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier {name!r}")
    return f'"{name}"'


def _exists(conn: psycopg.Connection, kind: str, name: str) -> bool:
    table = {
        "source": "rw_sources",
        "materialized_view": "rw_materialized_views",
        "sink": "rw_sinks",
    }[kind]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM rw_catalog.{table} WHERE name = %s",
            (name,),
        )
        return cur.fetchone() is not None


def ensure_stream_pipeline(cfg: RisingWaveConfig, spec: StreamPipelineSpec) -> None:
    src_name = f"src_{spec.pipeline_id}"
    mv_name = f"mv_{spec.pipeline_id}"
    sink_name = f"sink_{spec.pipeline_id}"

    with psycopg.connect(cfg.dsn, autocommit=True) as conn:
        if not _exists(conn, "source", src_name):
            create_source = (
                f"CREATE SOURCE {_ident(src_name)} (raw BYTEA) WITH ("
                f"connector='kafka', "
                f"topic='{spec.topic}', "
                f"properties.bootstrap.server='{spec.redpanda_brokers}', "
                f"scan.startup.mode='earliest'"
                f") FORMAT PLAIN ENCODE BYTES;"
            )
            with conn.cursor() as cur:
                cur.execute(create_source)
            logger.info("created RW source %s", src_name)

        if not _exists(conn, "materialized_view", mv_name):
            mv_sql = spec.mv_sql_override or (
                f"SELECT convert_from(raw, 'utf8')::JSONB AS payload, "
                f"now() AS ts FROM {_ident(src_name)}"
            )
            create_mv = f"CREATE MATERIALIZED VIEW {_ident(mv_name)} AS {mv_sql};"
            with conn.cursor() as cur:
                cur.execute(create_mv)
            logger.info("created RW MV %s", mv_name)

        if not _exists(conn, "sink", sink_name):
            create_sink = (
                f"CREATE SINK {_ident(sink_name)} FROM {_ident(mv_name)} WITH ("
                f"connector='deltalake', "
                f"type='append-only', "
                f"force_append_only='true', "
                f"location='{spec.delta_location}', "
                f"s3.endpoint='{spec.s3_endpoint}', "
                f"s3.access.key='{spec.s3_access_key}', "
                f"s3.secret.key='{spec.s3_secret_key}', "
                f"s3.region='{spec.s3_region}'"
                f");"
            )
            with conn.cursor() as cur:
                cur.execute(create_sink)
            logger.info("created RW sink %s -> %s", sink_name, spec.delta_location)


def drop_stream_pipeline(cfg: RisingWaveConfig, pipeline_id: str) -> None:
    """Idempotent teardown for ``lumid-data source delete``."""
    sink_name = f"sink_{pipeline_id}"
    mv_name = f"mv_{pipeline_id}"
    src_name = f"src_{pipeline_id}"
    with psycopg.connect(cfg.dsn, autocommit=True) as conn:
        for stmt in (
            f"DROP SINK IF EXISTS {_ident(sink_name)};",
            f"DROP MATERIALIZED VIEW IF EXISTS {_ident(mv_name)};",
            f"DROP SOURCE IF EXISTS {_ident(src_name)};",
        ):
            with conn.cursor() as cur:
                cur.execute(stmt)
