"""Schema-card render tests — the exact bytes the LLM sees in the prompt."""

from lumid_data.retrieve.schema_card import (
    ColumnCard,
    ForeignKeyHint,
    SchemaCard,
    SchemaCardBundle,
    VerifiedQuery,
    render_bundle_for_prompt,
    render_card_for_prompt,
)


def test_minimal_card_renders_table_name_and_columns():
    card = SchemaCard(
        fqname="schema.t",
        columns=[
            ColumnCard(name="id", type="int", nullable=False, is_pk=True),
        ],
    )
    text = render_card_for_prompt(card)
    assert "# Table: schema.t" in text
    assert "# Use SQL identifiers exactly as shown below." in text
    assert "(id:int PK,NOT NULL" in text


def test_card_quotes_case_sensitive_identifiers():
    card = SchemaCard(
        fqname="schema.t",
        columns=[
            ColumnCard(name="grossProfit", type="numeric", nullable=True),
            ColumnCard(name="operatingIncome", type="numeric", nullable=True),
            ColumnCard(name="plain_name", type="numeric", nullable=True),
        ],
    )
    text = render_card_for_prompt(card)
    assert '("grossProfit":numeric' in text
    assert '("operatingIncome":numeric' in text
    assert "(plain_name:numeric" in text


def test_card_includes_stats_when_present():
    card = SchemaCard(
        fqname="schema.t",
        approx_row_count=1234,
        columns=[
            ColumnCard(
                name="symbol",
                type="text",
                nullable=False,
                distinct_count=42,
                null_pct=0.05,
                sample_values=["NVDA", "AAPL"],
                top_k_values=[("NVDA", 100), ("AAPL", 50)],
                min="A",
                max="Z",
            ),
        ],
    )
    text = render_card_for_prompt(card)
    assert "Approx rows: 1234" in text
    assert "distinct=42" in text
    assert "null_pct=0.05" in text
    assert "samples=" in text and "'NVDA'" in text
    assert "top_k=" in text
    assert "min='A'" in text
    assert "max='Z'" in text


def test_card_renders_pk_and_fks():
    card = SchemaCard(
        fqname="schema.child",
        columns=[
            ColumnCard(name="id", type="int", nullable=False, is_pk=True),
            ColumnCard(name="parent_id", type="int", nullable=True, is_fk=True),
        ],
        pk=["id"],
        fks=[
            ForeignKeyHint(
                column="parent_id", ref_table="schema.parent", ref_column="id"
            )
        ],
    )
    text = render_card_for_prompt(card)
    assert "# Primary key: (id)" in text
    assert "# FK: parent_id -> schema.parent.id" in text


def test_card_renders_verified_queries_and_examples():
    card = SchemaCard(
        fqname="schema.t",
        columns=[ColumnCard(name="id", type="int", nullable=False)],
        verified_queries=[
            VerifiedQuery(intent="latest by id", sql="SELECT * FROM t LIMIT 1")
        ],
        example_questions=["how many rows are in t?"],
    )
    text = render_card_for_prompt(card)
    assert "# Verified queries:" in text
    assert "intent: latest by id" in text
    assert "# Example questions:" in text
    assert "- how many rows are in t?" in text


def test_bundle_renders_scope_header_and_glossary():
    bundle = SchemaCardBundle(
        scope="schema.t",
        tables=[
            SchemaCard(
                fqname="schema.t",
                columns=[ColumnCard(name="id", type="int", nullable=False)],
            ),
        ],
        glossary={"NVDA": "NVIDIA Corporation"},
        join_hints=["schema.t joins schema.u on t.id = u.t_id"],
    )
    text = render_bundle_for_prompt(bundle)
    assert "【DB scope: schema.t】" in text
    assert "# Glossary" in text
    assert "NVDA: NVIDIA Corporation" in text
    assert "# Join hints:" in text
    assert "schema.t joins schema.u on t.id = u.t_id" in text


def test_long_sample_values_are_truncated():
    card = SchemaCard(
        fqname="schema.t",
        columns=[
            ColumnCard(
                name="description",
                type="text",
                nullable=True,
                sample_values=["x" * 500],
            ),
        ],
    )
    text = render_card_for_prompt(card)
    # Truncation marker (the … ellipsis) should appear within the rendered samples
    assert "…" in text
