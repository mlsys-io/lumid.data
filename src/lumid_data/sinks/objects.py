"""Object sink: blob writes for image/audio/video/blob modalities.

Each blob lands at ``s3://<bucket>/<volume_path>/<sha256>`` (deterministic;
duplicate uploads no-op). Companion Delta manifest table holds
``(object_key, sha256, modality, content_type, size_bytes, ts)`` rows so
the lake stays queryable even when payloads are heterogeneous.

UC volumes (Phase 3) are the catalog-side identity for these blobs;
``catalog.unity.ensure_volume`` provisions them.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import boto3
import pyarrow as pa

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ObjectSinkConfig:
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"


def compute_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def object_key(volume_path: str, sha256: str) -> str:
    return f"{volume_path}/{sha256}"


def write_blob(
    cfg: ObjectSinkConfig,
    volume_path: str,
    sha256: str,
    payload: bytes,
    content_type: str | None,
) -> str:
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
        region_name=cfg.s3_region,
    )
    key = object_key(volume_path, sha256)
    extra: dict[str, str] = {}
    if content_type:
        extra["ContentType"] = content_type
    try:
        s3.head_object(Bucket=cfg.s3_bucket, Key=key)
        return f"s3://{cfg.s3_bucket}/{key}"
    except Exception:
        pass
    s3.put_object(Bucket=cfg.s3_bucket, Key=key, Body=payload, **extra)
    return f"s3://{cfg.s3_bucket}/{key}"


def manifest_row(
    object_uri: str,
    sha256: str,
    modality: str,
    content_type: str | None,
    size_bytes: int,
) -> pa.Table:
    """Arrow table with one row, conforming to the manifest schema."""
    fields: dict[str, pa.DataType] = {
        "object_uri": pa.string(),
        "sha256": pa.string(),
        "modality": pa.string(),
        "content_type": pa.string(),
        "size_bytes": pa.int64(),
        "ts": pa.timestamp("us", tz="UTC"),
    }
    return pa.table(
        {
            "object_uri": [object_uri],
            "sha256": [sha256],
            "modality": [modality],
            "content_type": [content_type or ""],
            "size_bytes": [size_bytes],
            "ts": [datetime.now(UTC)],
        },
        schema=pa.schema(fields),
    )
