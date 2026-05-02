"""Blob-modality handler for the agent.

Image/audio/video/blob payloads don't need schema inference; they need:

- a content hash (sha256) for deduplication and addressing
- a manifest row (object_uri, sha256, modality, content_type, size, ts)
- a stable object key under the dataset's UC volume

This module produces those artifacts. Sink execution (writing the blob
to MinIO + appending to the Delta manifest) lives in ``sinks/objects``.
"""

import hashlib
from datetime import UTC, datetime

import pyarrow as pa

from ..schemas.descriptors import Modality

_MANIFEST_FIELDS: dict[str, pa.DataType] = {
    "object_uri": pa.string(),
    "sha256": pa.string(),
    "modality": pa.string(),
    "content_type": pa.string(),
    "size_bytes": pa.int64(),
    "ts": pa.timestamp("us", tz="UTC"),
}
MANIFEST_SCHEMA = pa.schema(_MANIFEST_FIELDS)


def manifest_row(
    object_uri: str,
    sha256: str,
    modality: Modality,
    content_type: str | None,
    size_bytes: int,
) -> pa.Table:
    return pa.table(
        {
            "object_uri": [object_uri],
            "sha256": [sha256],
            "modality": [modality],
            "content_type": [content_type or ""],
            "size_bytes": [size_bytes],
            "ts": [datetime.now(UTC)],
        },
        schema=MANIFEST_SCHEMA,
    )


def compute_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
