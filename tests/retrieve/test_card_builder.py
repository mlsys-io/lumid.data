"""Pure-function tests for the schema-card builder helpers.

The DB-touching parts of :class:`SchemaCardBuilder` are exercised by the
live integration smoke; here we cover the edge-cases of scope parsing,
the ``*``-as-all-schemas expansion, and the custom Postgres-array parser.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from lumid_data.retrieve import card_builder as card_builder_module
from lumid_data.retrieve.card_builder import (
    SchemaCardBuilder,
    TableRef,
    _parse_pg_array,
    _split_scope_parts,
)


class TestSplitScopeParts:
    def test_simple_comma_split(self):
        wildcards, explicit = _split_scope_parts("a.b,c.d")
        assert wildcards == []
        assert explicit == [TableRef("a", "b"), TableRef("c", "d")]

    def test_strips_whitespace(self):
        wildcards, explicit = _split_scope_parts(" a.b , c.d ")
        assert wildcards == []
        assert explicit == [TableRef("a", "b"), TableRef("c", "d")]

    def test_drops_empty_segments(self):
        wildcards, explicit = _split_scope_parts("a.b,,c.d,")
        assert wildcards == []
        assert explicit == [TableRef("a", "b"), TableRef("c", "d")]

    def test_respects_double_quoted_segments(self):
        wildcards, explicit = _split_scope_parts('star.x,star."has,comma",star.z')
        assert wildcards == []
        assert explicit == [
            TableRef("star", "x"),
            TableRef("star", "has,comma"),
            TableRef("star", "z"),
        ]

    def test_respects_quoted_with_dash(self):
        wildcards, explicit = _split_scope_parts('star."fact-with-dash",star.y')
        assert wildcards == []
        assert explicit == [
            TableRef("star", "fact-with-dash"),
            TableRef("star", "y"),
        ]

    def test_quoted_schema_with_dot(self):
        wildcards, explicit = _split_scope_parts('"with dot.in name"."table"')
        assert wildcards == []
        assert explicit == [TableRef("with dot.in name", "table")]

    def test_wildcard_collects_schema(self):
        wildcards, explicit = _split_scope_parts("public.*, star.x")
        assert wildcards == ["public"]
        assert explicit == [TableRef("star", "x")]

    def test_wildcard_with_quoted_schema(self):
        wildcards, explicit = _split_scope_parts('"weird schema".*')
        assert wildcards == ["weird schema"]
        assert explicit == []

    def test_empty_input(self):
        wildcards, explicit = _split_scope_parts("")
        assert wildcards == []
        assert explicit == []
        wildcards, explicit = _split_scope_parts("   ")
        assert wildcards == []
        assert explicit == []

    @pytest.mark.parametrize(
        "value",
        [
            "schema",
            ".table",
            "schema.",
        ],
    )
    def test_rejects_malformed(self, value):
        with pytest.raises(ValueError):
            _split_scope_parts(value)


class TestParsePgArray:
    def test_none_returns_empty(self):
        assert _parse_pg_array(None) == []

    def test_empty_braces(self):
        assert _parse_pg_array("{}") == []

    def test_bare_strings(self):
        assert _parse_pg_array("{a,b,c}") == ["a", "b", "c"]

    def test_quoted_strings(self):
        assert _parse_pg_array('{"a","b","c"}') == ["a", "b", "c"]

    def test_quoted_with_special_chars(self):
        assert _parse_pg_array('{"hello world","foo,bar"}') == [
            "hello world",
            "foo,bar",
        ]

    def test_quoted_with_backslash_escape(self):
        # Postgres array literal: embedded `"` is `\"`, embedded `\` is `\\`.
        assert _parse_pg_array(r'{"with\"quote","with\\backslash"}') == [
            'with"quote',
            "with\\backslash",
        ]

    def test_skips_null_token(self):
        assert _parse_pg_array("{a,NULL,b}") == ["a", "b"]

    def test_numbers_as_text(self):
        assert _parse_pg_array("{1,2,3}") == ["1", "2", "3"]

    def test_returns_empty_for_non_array_text(self):
        assert _parse_pg_array("not-an-array") == []


def _patch_psycopg(
    monkeypatch, schemas: list[str], tables_by_schema: dict[str, list[str]]
):
    """Patch ``psycopg.AsyncConnection.connect`` with a scripted cursor.

    First ``execute`` call returns the schema list (``pg_namespace`` query);
    subsequent calls return the table list for whichever schema was queried.
    """
    cur = MagicMock()
    last_sql: list[str] = [""]
    last_params: list[tuple] = [()]

    async def execute(sql: str, params: tuple = ()) -> None:
        last_sql[0] = sql
        last_params[0] = params

    cur.execute = AsyncMock(side_effect=execute)

    async def fetchall() -> list[dict[str, str]]:
        if "pg_namespace" in last_sql[0]:
            return [{"nspname": s} for s in schemas]
        schema = last_params[0][0] if last_params[0] else ""
        return [{"table_name": n} for n in tables_by_schema.get(schema, [])]

    cur.fetchall = AsyncMock(side_effect=fetchall)

    @asynccontextmanager
    async def cursor_cm(*args, **kwargs):
        yield cur

    @asynccontextmanager
    async def conn_cm(*args, **kwargs):
        conn = MagicMock()
        conn.cursor = cursor_cm
        yield conn

    async def connect(*args, **kwargs):
        return conn_cm(*args, **kwargs)

    monkeypatch.setattr(
        card_builder_module.psycopg.AsyncConnection,
        "connect",
        AsyncMock(side_effect=connect),
    )
    return cur


class TestResolveScope:
    @pytest.mark.asyncio
    async def test_star_expands_to_all_user_schemas(self, monkeypatch):
        _patch_psycopg(
            monkeypatch,
            schemas=["public", "star"],
            tables_by_schema={
                "public": ["users"],
                "star": ["fact_news_metadata", "d_instrument_profile_fmp"],
            },
        )
        builder = SchemaCardBuilder(dsn="postgresql://x", role=None)
        tables = await builder._resolve_scope("*")
        assert sorted((t.schema, t.name) for t in tables) == [
            ("public", "users"),
            ("star", "d_instrument_profile_fmp"),
            ("star", "fact_news_metadata"),
        ]

    @pytest.mark.asyncio
    async def test_star_with_surrounding_whitespace(self, monkeypatch):
        _patch_psycopg(
            monkeypatch,
            schemas=["star"],
            tables_by_schema={"star": ["x"]},
        )
        builder = SchemaCardBuilder(dsn="postgresql://x", role=None)
        tables = await builder._resolve_scope("  *  ")
        assert [(t.schema, t.name) for t in tables] == [("star", "x")]

    @pytest.mark.asyncio
    async def test_explicit_scope_skips_schema_listing(self, monkeypatch):
        cur = _patch_psycopg(
            monkeypatch,
            schemas=["should_not_be_used"],
            tables_by_schema={"star": ["fact_x"]},
        )
        builder = SchemaCardBuilder(dsn="postgresql://x", role=None)
        tables = await builder._resolve_scope("star.fact_x")
        assert [(t.schema, t.name) for t in tables] == [("star", "fact_x")]
        executed = [call.args[0] for call in cur.execute.call_args_list]
        assert not any("pg_namespace" in sql for sql in executed)
