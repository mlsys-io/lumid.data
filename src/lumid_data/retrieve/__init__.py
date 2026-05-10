"""Retrieval support — schema cards, structured-plan validation, and replay.

The public entrypoint is the data agent. Retrieval is implemented as
deterministic tools behind ``/agent/v1``:

1. ``schema_card.SchemaCardBuilder`` — introspects Postgres into per-table cards
   (M-Schema-shaped: name, description, approx rowcount, columns with stats +
   sample values, PKs, FKs).
2. ``card_store.SchemaCardStore`` — persists cards as JSON in MinIO with TTL.
3. ``planner.parse_retrieval_plan`` — validates a structured plan emitted by
   the data agent.
4. ``replay.PlanReplayer`` — executes the plan deterministically (psycopg +
   MinIO) and materializes the result file under
   ``s3://<bucket>/retrievals/<run_id>/result.<fmt>``.

References: M-Schema (arxiv:2411.08599), CHESS (arxiv:2405.16755),
Recursive Language Models (arxiv:2512.24601).
"""

from .schema_card import (
    ColumnCard,
    ForeignKeyHint,
    SchemaCard,
    SchemaCardBundle,
    VerifiedQuery,
)

__all__ = [
    "ColumnCard",
    "ForeignKeyHint",
    "SchemaCard",
    "SchemaCardBundle",
    "VerifiedQuery",
]
