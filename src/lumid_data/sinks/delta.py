"""Delta Lake sink. Writes a pyarrow.Table to MinIO-backed Delta storage.

Goes through the Rust ``deltalake`` Python binding — no Spark dependency.
The location is derived from the IngestPlan's ``target_table`` (a UC
namespace path like ``lumid_data.<tenant>.<app>.<dataset>``) and the
configured S3 bucket.

Schema evolution: by default the binding appends with
``schema_mode="merge"`` (additive only). Stricter modes are available
through the descriptor's policy.
"""

import logging
from dataclasses import dataclass
from typing import Literal

import pyarrow as pa
from deltalake import write_deltalake
from deltalake.exceptions import DeltaError

logger = logging.getLogger(__name__)

SchemaMode = Literal["merge", "overwrite"]


@dataclass(frozen=True)
class DeltaSinkConfig:
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"
    allow_unsafe_rename: bool = True


def storage_options(cfg: DeltaSinkConfig) -> dict[str, str]:
    return {
        "AWS_ACCESS_KEY_ID": cfg.s3_access_key,
        "AWS_SECRET_ACCESS_KEY": cfg.s3_secret_key,
        "AWS_ENDPOINT_URL": cfg.s3_endpoint,
        "AWS_REGION": cfg.s3_region,
        "AWS_ALLOW_HTTP": "true",
        "AWS_S3_ALLOW_UNSAFE_RENAME": "true" if cfg.allow_unsafe_rename else "false",
    }


def table_uri(cfg: DeltaSinkConfig, target_table: str) -> str:
    path = target_table.replace(".", "/")
    return f"s3://{cfg.s3_bucket}/datasets/{path}"


def write(
    cfg: DeltaSinkConfig,
    target_table: str,
    table: pa.Table,
    *,
    partition_by: list[str] | None = None,
    schema_mode: SchemaMode = "merge",
) -> str:
    uri = table_uri(cfg, target_table)
    try:
        write_deltalake(
            uri,
            table,
            mode="append",
            schema_mode=schema_mode,
            partition_by=partition_by,
            storage_options=storage_options(cfg),
        )
    except DeltaError as exc:
        logger.exception("delta write failed for %s", target_table)
        raise RuntimeError(f"delta write failed: {exc}") from exc
    return uri
