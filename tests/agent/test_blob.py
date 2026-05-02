"""Blob-modality handler tests."""

from lumid_data.agent.blob import (
    MANIFEST_SCHEMA,
    compute_sha256,
    manifest_row,
)


def test_compute_sha256_deterministic() -> None:
    assert compute_sha256(b"hello") == compute_sha256(b"hello")
    assert compute_sha256(b"hello") != compute_sha256(b"world")


def test_manifest_row_shape() -> None:
    table = manifest_row(
        object_uri="s3://b/k/abc",
        sha256="abc",
        modality="image",
        content_type="image/png",
        size_bytes=42,
    )
    assert table.num_rows == 1
    assert set(table.column_names) >= {
        "object_uri",
        "sha256",
        "modality",
        "content_type",
        "size_bytes",
        "ts",
    }
    assert table.schema == MANIFEST_SCHEMA
