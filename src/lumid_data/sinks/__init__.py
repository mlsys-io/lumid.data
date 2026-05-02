"""Execution stage: write IngestPlan output to the chosen sink."""

from . import delta, dlq

__all__ = ["delta", "dlq"]
