"""Execution stage: write IngestPlan output to the chosen sink."""

from . import delta, dlq, redpanda, risingwave

__all__ = ["delta", "dlq", "redpanda", "risingwave"]
