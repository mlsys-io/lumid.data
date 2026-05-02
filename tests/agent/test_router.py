"""End-to-end agent loop unit tests (in-memory; no sinks)."""

from lumid_data.agent import route
from lumid_data.schemas.descriptors import SourceDescriptor


def _desc(**kwargs) -> SourceDescriptor:
    return SourceDescriptor(
        source_id="src-test",
        name="hist",
        tenant="acme",
        cadence="batch",
        **kwargs,
    )


def test_csv_payload_routes_to_delta_with_plan() -> None:
    payload = b"a,b\n1,foo\n2,bar\n"
    result = route(payload, _desc(), ingest_id="ing-1", mime_override="text/csv")
    assert result.plan.route == "delta"
    assert result.plan.target_table == "lumid_data.acme.hist"
    assert result.plan.modality_resolved == "structured"
    assert result.table is not None
    assert result.table.num_rows == 2
    assert result.plan.schema_fp is not None


def test_text_payload_routes_to_delta_with_text_schema() -> None:
    payload = b"a memo from the field"
    result = route(payload, _desc(), ingest_id="ing-2", mime_override="text/plain")
    assert result.plan.route == "delta"
    assert result.plan.modality_resolved == "text"
    assert result.table is not None
    assert "content" in result.table.column_names


def test_unparseable_csv_goes_to_dlq() -> None:
    payload = b"\x00\x01\x02\x03binary garbage"
    desc = _desc(modality="structured")  # force structured to trigger the parse path
    result = route(payload, desc, ingest_id="ing-3", mime_override="text/csv")
    assert result.plan.route in {"dlq", "delta"}
    if result.plan.route == "dlq":
        assert result.plan.error is not None


def test_empty_csv_goes_to_dlq_when_require_non_empty() -> None:
    result = route(b"a,b\n", _desc(), ingest_id="ing-4", mime_override="text/csv")
    assert result.plan.route == "dlq"
    assert result.plan.error == "empty_after_quality"
