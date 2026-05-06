"""Pure-function tests for the schema-card builder helpers.

The DB-touching parts of :class:`SchemaCardBuilder` are exercised by the
live integration smoke; here we cover the edge-cases of scope parsing
and the custom Postgres-array parser.
"""

import pytest

from lumid_data.retrieve.card_builder import (
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
