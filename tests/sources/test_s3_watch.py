"""S3 watch pull connector tests."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from lumid_data.sources.s3_watch import S3WatchSpec, fetch, list_new_objects


def _spec() -> S3WatchSpec:
    return S3WatchSpec(
        endpoint_url="http://minio:9000",
        access_key="ak",
        secret_key="sk",
        bucket="b",
        prefix="p/",
    )


def test_list_new_filters_by_since() -> None:
    cutoff = datetime(2026, 5, 2, 0, 0, 0, tzinfo=UTC)
    fake_s3 = MagicMock()
    fake_paginator = MagicMock()
    fake_paginator.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "p/old.txt",
                    "Size": 1,
                    "LastModified": datetime(2026, 4, 1, tzinfo=UTC),
                    "ETag": "old",
                },
                {
                    "Key": "p/new.txt",
                    "Size": 2,
                    "LastModified": datetime(2026, 5, 3, tzinfo=UTC),
                    "ETag": "new",
                },
            ]
        }
    ]
    fake_s3.get_paginator.return_value = fake_paginator
    with patch("boto3.client", return_value=fake_s3):
        new = list_new_objects(_spec(), since=cutoff)
    assert [o["key"] for o in new] == ["p/new.txt"]


def test_fetch_returns_bytes() -> None:
    fake_body = MagicMock()
    fake_body.read.return_value = b"hello"
    fake_s3 = MagicMock()
    fake_s3.get_object.return_value = {"Body": fake_body}
    with patch("boto3.client", return_value=fake_s3):
        data = fetch(_spec(), "p/x.txt")
    assert data == b"hello"
