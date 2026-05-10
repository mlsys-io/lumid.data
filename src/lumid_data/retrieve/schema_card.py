"""Schema-card data model — M-Schema-shaped cards the planner reads.

Per-table card carries the stats + sample values an LLM planner needs to
emit canonical SQL without enumerating ``information_schema`` at runtime.
Cards are built by :class:`SchemaCardBuilder` (see ``card_builder.py``)
and cached in object storage by :class:`SchemaCardStore`
(see ``card_store.py``).

Format borrows from M-Schema (Alibaba XiYan-SQL, arxiv:2411.08599) +
Snowflake Cortex Analyst's YAML semantic model (verified queries,
synonyms) + Databricks Genie (value dictionaries).
"""

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ColumnCard(BaseModel):
    """Per-column slice of a table card.

    Stats come from ``pg_stats`` (free, populated by ANALYZE); sample
    values come from cheap ``SELECT DISTINCT … LIMIT 5`` probes.
    """

    name: str
    type: str  # SQL type, e.g. "text", "numeric", "timestamptz"
    nullable: bool
    description: str | None = None
    is_pk: bool = False
    is_fk: bool = False
    distinct_count: int | None = None
    null_pct: float | None = None
    sample_values: list[Any] = Field(default_factory=list, max_length=10)
    top_k_values: list[tuple[Any, int]] = Field(default_factory=list, max_length=10)
    min: Any | None = None
    max: Any | None = None
    format_regex: str | None = None
    synonyms: list[str] = Field(default_factory=list)


class ForeignKeyHint(BaseModel):
    column: str
    ref_table: str  # fully-qualified, e.g. "star.d_instrument_profile_fmp"
    ref_column: str


class VerifiedQuery(BaseModel):
    """A curator-supplied gold SQL pattern for few-shot prompting."""

    intent: str
    sql: str


class SchemaCard(BaseModel):
    fqname: str  # "schema.table"
    description: str | None = None
    approx_row_count: int | None = None
    update_freshness: str | None = None  # e.g. "daily", "hourly"
    columns: list[ColumnCard]
    pk: list[str] = Field(default_factory=list)
    fks: list[ForeignKeyHint] = Field(default_factory=list)
    example_questions: list[str] = Field(default_factory=list)
    verified_queries: list[VerifiedQuery] = Field(default_factory=list)
    governance: dict[str, Any] = Field(default_factory=dict)
    built_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SchemaCardBundle(BaseModel):
    """A bundle of cards plus optional cross-table hints, ready for prompting."""

    scope: str  # the scope identifier the bundle was built for
    tables: list[SchemaCard]
    join_hints: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)


def render_card_for_prompt(card: SchemaCard) -> str:
    """Render a single :class:`SchemaCard` into the M-Schema-style block."""
    lines: list[str] = [f"# Table: {card.fqname}"]
    lines.append("# Use SQL identifiers exactly as shown below.")
    if card.description:
        lines.append(f"# Description: {card.description}")
    if card.approx_row_count is not None:
        lines.append(f"# Approx rows: {card.approx_row_count}")
    if card.update_freshness:
        lines.append(f"# Update freshness: {card.update_freshness}")
    lines.append("[")
    for col in card.columns:
        flags: list[str] = []
        if col.is_pk:
            flags.append("PK")
        if col.is_fk:
            flags.append("FK")
        if not col.nullable:
            flags.append("NOT NULL")
        flag_str = (" " + ",".join(flags)) if flags else ""
        bits: list[str] = [f"  ({_sql_identifier(col.name)}:{col.type}{flag_str}"]
        if col.description:
            bits.append(f", {col.description}")
        if col.distinct_count is not None:
            bits.append(f", distinct={col.distinct_count}")
        if col.null_pct is not None and col.null_pct > 0:
            bits.append(f", null_pct={col.null_pct:.2f}")
        if col.min is not None and col.max is not None:
            bits.append(f", min={col.min!r}, max={col.max!r}")
        if col.sample_values:
            previews = [_truncate(repr(v), 60) for v in col.sample_values[:5]]
            bits.append(f", samples=[{', '.join(previews)}]")
        if col.top_k_values:
            tk = ", ".join(
                f"{_truncate(repr(v), 30)}({n})" for v, n in col.top_k_values[:5]
            )
            bits.append(f", top_k=[{tk}]")
        if col.format_regex:
            bits.append(f", format={col.format_regex!r}")
        if col.synonyms:
            bits.append(f", synonyms={col.synonyms}")
        bits.append(")")
        lines.append("".join(bits) + ",")
    lines.append("]")
    if card.pk:
        lines.append(f"# Primary key: ({', '.join(card.pk)})")
    if card.fks:
        for fk in card.fks:
            lines.append(f"# FK: {fk.column} -> {fk.ref_table}.{fk.ref_column}")
    if card.verified_queries:
        lines.append("# Verified queries:")
        for vq in card.verified_queries:
            lines.append(f"#   intent: {vq.intent}")
            lines.append(f"#   sql:   {_truncate(vq.sql, 200)}")
    if card.example_questions:
        lines.append("# Example questions:")
        for q in card.example_questions:
            lines.append(f"#   - {q}")
    return "\n".join(lines)


_SIMPLE_SQL_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _sql_identifier(name: str) -> str:
    if _SIMPLE_SQL_IDENTIFIER.fullmatch(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def render_bundle_for_prompt(bundle: SchemaCardBundle) -> str:
    """Render a bundle of cards into the system-prompt addendum."""
    parts: list[str] = [f"【DB scope: {bundle.scope}】"]
    if bundle.glossary:
        parts.append("# Glossary")
        for term, defn in bundle.glossary.items():
            parts.append(f"#   {term}: {defn}")
    for card in bundle.tables:
        parts.append("")
        parts.append(render_card_for_prompt(card))
    if bundle.join_hints:
        parts.append("")
        parts.append("# Join hints:")
        for hint in bundle.join_hints:
            parts.append(f"#   - {hint}")
    return "\n".join(parts)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# Sentinel for when a builder can't produce a stat (e.g. column has no
# pg_stats entry yet — table never ANALYZEd).
UNKNOWN_STAT: Literal["__unknown__"] = "__unknown__"
