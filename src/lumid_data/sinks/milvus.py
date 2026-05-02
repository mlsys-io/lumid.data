"""Milvus sink for multi-modal embeddings.

A collection per dataset (``lumid_data_<source_id>``); the standard
schema is ``(id INT64 primary, vector FLOAT_VECTOR, sha256 VARCHAR,
modality VARCHAR, ts TIMESTAMP)``. Vectors are inserted in batches; the
embedder pipeline (FlowMesh ``inference`` task) populates ``vector``
from the raw object referenced by ``sha256`` in the Delta manifest.
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MilvusSinkConfig:
    uri: str
    user: str | None = None
    password: str | None = None
    db_name: str = "default"


def _connect(cfg: MilvusSinkConfig) -> None:
    from pymilvus import connections

    connections.connect(
        alias="default",
        uri=cfg.uri,
        user=cfg.user or "",
        password=cfg.password or "",
        db_name=cfg.db_name,
    )


def ensure_collection(cfg: MilvusSinkConfig, name: str, dim: int) -> None:
    from pymilvus import (
        Collection,
        CollectionSchema,
        DataType,
        FieldSchema,
        utility,
    )

    _connect(cfg)
    if utility.has_collection(name):
        return
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
        FieldSchema(name="sha256", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="modality", dtype=DataType.VARCHAR, max_length=16),
        FieldSchema(name="ts", dtype=DataType.INT64),
    ]
    schema = CollectionSchema(
        fields=fields,
        description=f"lumid.data embeddings for {name}",
    )
    coll = Collection(name=name, schema=schema)
    coll.create_index(
        field_name="vector",
        index_params={
            "metric_type": "IP",
            "index_type": "AUTOINDEX",
            "params": {},
        },
    )
    coll.load()
    logger.info("created Milvus collection %s (dim=%d)", name, dim)


def insert(
    cfg: MilvusSinkConfig,
    name: str,
    vectors: list[list[float]],
    sha256s: list[str],
    modalities: list[str],
    timestamps_us: list[int],
) -> int:
    from pymilvus import Collection

    if not (len(vectors) == len(sha256s) == len(modalities) == len(timestamps_us)):
        raise ValueError("milvus.insert: column lengths must match")
    _connect(cfg)
    coll = Collection(name=name)
    data: list[Any] = [vectors, sha256s, modalities, timestamps_us]
    result = coll.insert(data)
    coll.flush()
    inserted = int(getattr(result, "insert_count", len(vectors)))
    return inserted
