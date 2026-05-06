"""Retrieval pipeline — schema-card-driven NL2SQL planning + replay.

The pipeline is internal to ``/retrieve/v1`` (see ``server/routers/retrieve.py``):

1. ``schema_card.SchemaCardBuilder`` — introspects Postgres into per-table cards
   (M-Schema-shaped: name, description, approx rowcount, columns with stats +
   sample values, PKs, FKs).
2. ``card_store.SchemaCardStore`` — persists cards as JSON in MinIO with TTL.
3. ``planner.RetrievalPlanner`` — builds the planner prompt with the relevant
   slice of cards + few-shot system prompt, drives ``/agent/v1`` internally
   with probe-only tools, parses the agent's JSON plan.
4. ``replay.PlanReplayer`` — executes the plan deterministically (psycopg +
   MinIO), retries once on SQL error with the error message fed back to the
   agent, materializes the result file under
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
