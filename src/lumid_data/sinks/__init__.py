"""Execution stage: write IngestPlan output to the chosen sink."""

from . import delta, dlq, milvus, objects, redpanda, risingwave, timescale

__all__ = [
    "delta",
    "dlq",
    "milvus",
    "objects",
    "redpanda",
    "risingwave",
    "timescale",
]
