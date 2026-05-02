"""Objects sink unit tests."""

from unittest.mock import MagicMock, patch

from lumid_data.sinks.objects import (
    ObjectSinkConfig,
    compute_sha256,
    object_key,
    write_blob,
)


def _cfg() -> ObjectSinkConfig:
    return ObjectSinkConfig(
        s3_endpoint="http://minio:9000",
        s3_access_key="ak",
        s3_secret_key="sk",
        s3_bucket="lumid-data",
    )


def test_compute_sha256_deterministic() -> None:
    assert compute_sha256(b"x") == compute_sha256(b"x")


def test_object_key_format() -> None:
    assert object_key("vol/a/b", "abc123") == "vol/a/b/abc123"


def test_write_blob_skips_when_already_present() -> None:
    fake_s3 = MagicMock()
    fake_s3.head_object.return_value = {}
    with patch("boto3.client", return_value=fake_s3):
        uri = write_blob(_cfg(), "vol/a/b", "abc", b"x", "image/png")
    fake_s3.put_object.assert_not_called()
    assert uri == "s3://lumid-data/vol/a/b/abc"


def test_write_blob_uploads_when_missing() -> None:
    fake_s3 = MagicMock()
    fake_s3.head_object.side_effect = Exception("404")
    with patch("boto3.client", return_value=fake_s3):
        uri = write_blob(_cfg(), "vol/a/b", "abc", b"hello", "image/png")
    fake_s3.put_object.assert_called_once()
    assert uri == "s3://lumid-data/vol/a/b/abc"
