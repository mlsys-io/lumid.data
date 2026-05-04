"""S3 helpers unit tests with botocore Stubber."""

import pytest

from lumid_data.server.services.s3 import (
    S3Config,
    compute_sha256,
    delete,
    list_objects,
    put_idempotent,
    stream_get,
)


@pytest.fixture
def s3():
    pytest.importorskip("moto")
    import boto3
    from moto import mock_aws

    with mock_aws():
        cfg = S3Config(
            endpoint="",  # moto intercepts boto3.client without an endpoint_url
            access_key="ak",
            secret_key="sk",
            region="us-east-1",
            default_bucket="lumid-data",
        )
        client = boto3.client(
            "s3",
            aws_access_key_id="ak",
            aws_secret_access_key="sk",
            region_name="us-east-1",
        )
        client.create_bucket(Bucket="lumid-data")
        yield cfg, client


def test_put_idempotent_skips_existing(s3) -> None:
    cfg, client = s3
    uri1 = put_idempotent(client, cfg.default_bucket, "x.txt", b"hi", "text/plain")
    uri2 = put_idempotent(client, cfg.default_bucket, "x.txt", b"hi", "text/plain")
    assert uri1 == uri2 == "s3://lumid-data/x.txt"


def test_put_then_stream_get_roundtrip(s3) -> None:
    cfg, client = s3
    put_idempotent(client, cfg.default_bucket, "x.txt", b"hello", "text/plain")
    chunks, meta = stream_get(client, cfg.default_bucket, "x.txt")
    body = b"".join(chunks)
    assert body == b"hello"
    assert meta["content_type"] == "text/plain"


def test_list_objects(s3) -> None:
    cfg, client = s3
    for i in range(3):
        put_idempotent(client, cfg.default_bucket, f"a/{i}", b"x", None)
    items = list_objects(client, cfg.default_bucket, prefix="a/", limit=10)
    assert len(items) == 3
    assert all(item["key"].startswith("a/") for item in items)


def test_delete(s3) -> None:
    cfg, client = s3
    put_idempotent(client, cfg.default_bucket, "y.txt", b"x", None)
    delete(client, cfg.default_bucket, "y.txt")
    items = list_objects(client, cfg.default_bucket, prefix="y.txt")
    assert items == []


def test_compute_sha256_deterministic() -> None:
    assert compute_sha256(b"x") == compute_sha256(b"x")
