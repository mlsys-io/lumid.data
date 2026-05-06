"""Build :class:`SchemaCard` instances from a Postgres database.

Stats come from ``pg_stats`` (free, populated by ``ANALYZE``) — that
gives us distinct count, null %, top-K most-common values + frequencies,
and histogram bounds (min/max + sample buckets) without ever doing a
full table scan. Falls back to ``information_schema`` for nullability
and ``pg_attribute`` / ``pg_constraint`` for the structural pieces
(columns, PKs, FKs).

Caveat: tables that have never been ``ANALYZE``-d won't have
``pg_stats`` rows. The card still gets columns / types / PKs / FKs; the
stat fields just stay ``None``. Run ``ANALYZE schema.table`` to populate.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .schema_card import (
    ColumnCard,
    ForeignKeyHint,
    SchemaCard,
    SchemaCardBundle,
)

logger = logging.getLogger(__name__)


def _split_top_level(s: str, delim: str) -> list[str]:
    """Split ``s`` on ``delim`` while respecting double-quoted segments.

    Inside ``"…"`` the delimiter is ignored, and the SQL-style escape
    ``""`` (a doubled quote) is taken to mean an embedded ``"`` that
    keeps the quoted block open. Outer quotes stay on each returned
    piece — callers strip them when needed.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if in_quote:
            if ch == '"':
                if i + 1 < n and s[i + 1] == '"':
                    buf.append('""')
                    i += 2
                    continue
                in_quote = False
                buf.append(ch)
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_quote = True
            buf.append(ch)
            i += 1
            continue
        if ch == delim:
            parts.append("".join(buf))
            buf.clear()
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _strip_outer_quotes(s: str) -> str:
    """Strip the outer ``"…"`` wrapper if present, undoing ``""`` escapes."""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('""', '"')
    return s


def _split_scope_parts(scope: str) -> tuple[list[str], list["TableRef"]]:
    """Tokenize a scope spec into wildcard schemas + explicit table refs."""
    wildcards: list[str] = []
    explicit: list[TableRef] = []
    for raw_part in _split_top_level(scope, ","):
        part = raw_part.strip()
        if not part:
            continue
        if part.endswith(".*"):
            wildcards.append(_strip_outer_quotes(part[:-2].strip()))
            continue
        cols = _split_top_level(part, ".")
        if len(cols) != 2 or not cols[0].strip() or not cols[1].strip():
            raise ValueError(f"invalid scope entry: {part!r}")
        explicit.append(
            TableRef(
                schema=_strip_outer_quotes(cols[0].strip()),
                name=_strip_outer_quotes(cols[1].strip()),
            )
        )
    return wildcards, explicit


@dataclass(frozen=True)
class TableRef:
    schema: str
    name: str

    @property
    def fqname(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def regclass_literal(self) -> str:
        return f'"{self.schema}"."{self.name}"'


@dataclass(frozen=True)
class _ColumnStat:
    distinct_count: int | None
    null_pct: float | None
    sample_values: list[object]
    top_k_values: list[tuple[object, int]]
    min: object | None
    max: object | None


class SchemaCardBuilder:
    """Builds :class:`SchemaCardBundle` over a Postgres connection."""

    def __init__(self, dsn: str, role: str | None = None) -> None:
        self._dsn = dsn
        self._role = role

    async def build(self, scope: str) -> SchemaCardBundle:
        tables = await self._resolve_scope(scope)
        cards: list[SchemaCard] = []
        async with await psycopg.AsyncConnection.connect(
            self._dsn, autocommit=True
        ) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if self._role:
                    await cur.execute(f"SET LOCAL ROLE {self._role}")
                for table in tables:
                    try:
                        cards.append(await self._build_card(cur, table))
                    except Exception:
                        logger.exception("card build failed for %s", table.fqname)
        return SchemaCardBundle(scope=scope, tables=cards)

    async def _resolve_scope(self, scope: str) -> list[TableRef]:
        """Expand a scope spec into a concrete list of tables.

        Supported forms:
          - ``"schema.*"`` — all base tables in ``schema``
          - ``"schema.table"`` — single table
          - comma-separated combinations of the above
        """
        wildcards, explicit = _split_scope_parts(scope)
        resolved: list[TableRef] = list(explicit)
        if wildcards:
            async with await psycopg.AsyncConnection.connect(
                self._dsn, autocommit=True
            ) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    if self._role:
                        await cur.execute(f"SET LOCAL ROLE {self._role}")
                    for schema in wildcards:
                        await cur.execute(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
                            "ORDER BY table_name",
                            (schema,),
                        )
                        rows = [dict(r) for r in await cur.fetchall()]
                        for row in rows:
                            resolved.append(
                                TableRef(schema=schema, name=row["table_name"])
                            )
        return _dedupe_tables(resolved)

    async def _build_card(self, cur: Any, table: TableRef) -> SchemaCard:
        approx_rows = await self._approx_rowcount(cur, table)
        description = await self._table_comment(cur, table)
        columns = await self._columns(cur, table)
        pk_cols = await self._primary_key_columns(cur, table)
        fks = await self._foreign_keys(cur, table)
        stats = await self._column_stats(cur, table)

        pk_set = set(pk_cols)
        fk_set = {fk.column for fk in fks}
        for col in columns:
            col.is_pk = col.name in pk_set
            col.is_fk = col.name in fk_set
            stat = stats.get(col.name)
            if stat:
                col.distinct_count = stat.distinct_count
                col.null_pct = stat.null_pct
                col.sample_values = stat.sample_values
                col.top_k_values = stat.top_k_values
                col.min = stat.min
                col.max = stat.max

        return SchemaCard(
            fqname=table.fqname,
            description=description,
            approx_row_count=approx_rows,
            columns=columns,
            pk=pk_cols,
            fks=fks,
        )

    async def _approx_rowcount(self, cur: Any, table: TableRef) -> int | None:
        await cur.execute(
            "SELECT reltuples::bigint AS n FROM pg_class WHERE oid = (%s)::regclass",
            (table.regclass_literal,),
        )
        fetched = await cur.fetchone()
        if fetched is None:
            return None
        n = dict(fetched).get("n")
        return None if n is None or n < 0 else int(n)

    async def _table_comment(self, cur: Any, table: TableRef) -> str | None:
        await cur.execute(
            "SELECT obj_description((%s)::regclass) AS comment",
            (table.regclass_literal,),
        )
        fetched = await cur.fetchone()
        return None if fetched is None else dict(fetched).get("comment")

    async def _columns(self, cur: Any, table: TableRef) -> list[ColumnCard]:
        await cur.execute(
            """
            SELECT
                a.attname                                  AS name,
                format_type(a.atttypid, a.atttypmod)       AS type,
                NOT a.attnotnull                           AS nullable,
                col_description(a.attrelid, a.attnum)      AS description
            FROM pg_attribute a
            WHERE a.attrelid = (%s)::regclass
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            (table.regclass_literal,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        return [
            ColumnCard(
                name=row["name"],
                type=row["type"],
                nullable=bool(row["nullable"]),
                description=row.get("description"),
            )
            for row in rows
        ]

    async def _primary_key_columns(self, cur: Any, table: TableRef) -> list[str]:
        await cur.execute(
            """
            SELECT a.attname AS name
            FROM pg_index i
            JOIN pg_attribute a
              ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = (%s)::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
            """,
            (table.regclass_literal,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        return [row["name"] for row in rows]

    async def _foreign_keys(self, cur: Any, table: TableRef) -> list[ForeignKeyHint]:
        await cur.execute(
            """
            SELECT
                child_a.attname  AS column,
                refclass.relname AS ref_table,
                refschema.nspname AS ref_schema,
                ref_a.attname    AS ref_column
            FROM pg_constraint c
            JOIN pg_class childclass    ON childclass.oid = c.conrelid
            JOIN pg_class refclass      ON refclass.oid = c.confrelid
            JOIN pg_namespace refschema ON refschema.oid = refclass.relnamespace
            JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, idx)
                ON true
            JOIN unnest(c.confkey) WITH ORDINALITY AS r(attnum, idx)
                ON r.idx = k.idx
            JOIN pg_attribute child_a
                ON child_a.attrelid = c.conrelid AND child_a.attnum = k.attnum
            JOIN pg_attribute ref_a
                ON ref_a.attrelid = c.confrelid AND ref_a.attnum = r.attnum
            WHERE c.conrelid = (%s)::regclass AND c.contype = 'f'
            ORDER BY c.conname, k.idx
            """,
            (table.regclass_literal,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        return [
            ForeignKeyHint(
                column=row["column"],
                ref_table=f'{row["ref_schema"]}.{row["ref_table"]}',
                ref_column=row["ref_column"],
            )
            for row in rows
        ]

    async def _column_stats(self, cur: Any, table: TableRef) -> dict[str, _ColumnStat]:
        await cur.execute(
            """
            SELECT
                attname,
                n_distinct,
                null_frac,
                most_common_vals::text  AS mcv_text,
                most_common_freqs,
                histogram_bounds::text  AS hist_text
            FROM pg_stats
            WHERE schemaname = %s AND tablename = %s
            """,
            (table.schema, table.name),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        out: dict[str, _ColumnStat] = {}
        for row in rows:
            mcv = _parse_pg_array(row.get("mcv_text"))
            freqs = list(row.get("most_common_freqs") or [])
            hist = _parse_pg_array(row.get("hist_text"))
            n_distinct_raw = row.get("n_distinct")
            distinct_count: int | None = None
            if isinstance(n_distinct_raw, (int, float)) and n_distinct_raw >= 0:
                distinct_count = int(n_distinct_raw)
            null_pct: float | None = None
            null_frac = row.get("null_frac")
            if null_frac is not None:
                null_pct = float(null_frac)
            top_k: list[tuple[object, int]] = []
            for value, freq in zip(mcv, freqs):
                top_k.append((value, int(freq * 1_000)))
            samples: list[object] = list(mcv[:5])
            if not samples and hist:
                samples = list(hist[:5])
            min_val = hist[0] if hist else None
            max_val = hist[-1] if hist else None
            out[row["attname"]] = _ColumnStat(
                distinct_count=distinct_count,
                null_pct=null_pct,
                sample_values=samples,
                top_k_values=top_k,
                min=min_val,
                max=max_val,
            )
        return out


def _dedupe_tables(items: Iterable[TableRef]) -> list[TableRef]:
    seen: set[tuple[str, str]] = set()
    out: list[TableRef] = []
    for t in items:
        key = (t.schema, t.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _parse_pg_array(text: str | None) -> list[str]:
    """Parse the canonical Postgres-array text format (``{a,b,"c d"}``).

    psycopg returns ``most_common_vals::text`` and ``histogram_bounds::text``
    as the canonical Postgres array literal. We strip the enclosing braces,
    walk the inner text token-by-token: a token is either a ``"…"`` block
    (with ``\\"`` and ``\\\\`` escapes) or a bare run up to the next comma.
    Bare ``NULL`` and empty bare tokens are dropped.
    """
    if text is None:
        return []
    s = text.strip()
    if not s.startswith("{") or not s.endswith("}"):
        return []
    inner = s[1:-1]
    if not inner:
        return []
    out: list[str] = []
    i, n = 0, len(inner)
    while i < n:
        if inner[i] == '"':
            buf: list[str] = []
            i += 1
            while i < n:
                ch = inner[i]
                if ch == "\\" and i + 1 < n:
                    buf.append(inner[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    break
                buf.append(ch)
                i += 1
            out.append("".join(buf))
        else:
            j = inner.find(",", i)
            end = n if j == -1 else j
            piece = inner[i:end].strip()
            if piece and piece != "NULL":
                out.append(piece)
            i = end
        if i < n and inner[i] == ",":
            i += 1
    return out
