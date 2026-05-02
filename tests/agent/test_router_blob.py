"""Agent loop on blob payloads."""

from lumid_data.agent import route
from lumid_data.schemas.descriptors import SourceDescriptor


def _img() -> SourceDescriptor:
    return SourceDescriptor(
        source_id="src-img",
        name="benchmarks",
        tenant="acme",
        cadence="batch",
        modality="image",
    )


def test_image_routes_to_uc_volume_with_manifest() -> None:
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    result = route(payload, _img(), ingest_id="ing-img", mime_override="image/png")
    assert result.plan.route == "uc_volume_with_manifest"
    assert result.plan.target_volume is not None
    assert result.plan.target_table is not None
    assert result.table is not None
    assert result.table.num_rows == 1
    assert "sha256" in result.table.column_names
    assert "object_uri" in result.table.column_names


def test_blob_modality_with_explicit_descriptor() -> None:
    desc = SourceDescriptor(
        source_id="src-blob",
        name="raw",
        tenant="acme",
        cadence="batch",
        modality="blob",
    )
    payload = b"\x00\x01\x02\x03\x04"
    result = route(payload, desc, ingest_id="ing-blob")
    assert result.plan.route == "uc_volume_with_manifest"
    assert result.plan.modality_resolved == "blob"
    assert result.table is not None
