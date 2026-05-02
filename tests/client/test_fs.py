"""client.fs() shim tests."""

from unittest.mock import patch

import pyarrow as pa

from lumid_data.client import Client


def _client(tmp_path) -> Client:
    return Client(
        base_url="http://localhost:9100",
        token="t",
        s3_endpoint="http://minio:9000",
        s3_access_key="ak",
        s3_secret_key="sk",
    )


def test_fs_write_bytes_calls_ingest(tmp_path) -> None:
    c = _client(tmp_path)
    with patch.object(Client, "ingest", return_value={"status": "ok"}) as ing:
        c.fs("src-1").write_bytes(b"data", mime="image/png")
    ing.assert_called_once_with("src-1", b"data", mime="image/png")


def test_fs_read_blobs_pulls_from_manifest(tmp_path) -> None:
    c = _client(tmp_path)
    fake_table = pa.table({"object_uri": ["s3://b/k/a", "s3://b/k/b"]})
    with (
        patch.object(
            Client, "list_datasets", return_value=[{"table_uri": "s3://b/manifest"}]
        ),
        patch.object(Client, "_read_delta_uri", return_value=fake_table),
    ):
        blobs = c.fs("src-1").read_blobs()
    assert blobs == ["s3://b/k/a", "s3://b/k/b"]


def test_fs_read_blobs_empty_when_no_dataset(tmp_path) -> None:
    c = _client(tmp_path)
    with patch.object(Client, "list_datasets", return_value=[]):
        assert c.fs("src-1").read_blobs() == []
