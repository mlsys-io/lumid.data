"""Schema inference + reconcile unit tests."""

import pyarrow as pa

from lumid_data.agent.schema import fingerprint, infer, reconcile


def _schema(fields: dict[str, pa.DataType]) -> pa.Schema:
    return pa.schema(fields)


def test_csv_inference() -> None:
    table = infer(b"x,y\n1,2\n3,4\n", "structured", mime_hint="text/csv")
    assert set(table.column_names) == {"x", "y"}
    assert table.num_rows == 2


def test_json_array_inference() -> None:
    table = infer(b'[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]', "structured")
    assert set(table.column_names) == {"a", "b"}
    assert table.num_rows == 2


def test_text_wraps_into_fixed_schema() -> None:
    table = infer(b"hello", "text", mime_hint="text/plain")
    assert table.num_rows == 1
    assert "content" in table.column_names
    assert "mime" in table.column_names
    assert "ts" in table.column_names


def test_fingerprint_stable() -> None:
    schema = _schema({"a": pa.int64(), "b": pa.string()})
    assert fingerprint(schema) == fingerprint(schema)


def test_reconcile_no_existing_schema_is_not_drift() -> None:
    new = _schema({"a": pa.int64()})
    is_drift, issues = reconcile(new, None)
    assert is_drift is False
    assert issues == []


def test_reconcile_additive_field_flagged() -> None:
    existing = _schema({"a": pa.int64()})
    new = _schema({"a": pa.int64(), "b": pa.string()})
    is_drift, issues = reconcile(new, existing)
    assert is_drift is True
    assert any("new fields" in x for x in issues)


def test_reconcile_type_mismatch_flagged() -> None:
    existing = _schema({"a": pa.int64()})
    new = _schema({"a": pa.string()})
    is_drift, issues = reconcile(new, existing)
    assert is_drift is True
    assert any("type mismatch" in x for x in issues)
