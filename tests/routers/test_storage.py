"""``/storage/v1/bucket/{bucket}`` ensure-bucket endpoint."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lumid_data.server.routers.storage import ensure_bucket as ensure_bucket_route


@pytest.mark.asyncio
async def test_ensure_bucket_first_call_reports_created() -> None:
    pytest.importorskip("moto")
    import boto3
    from moto import mock_aws

    state = MagicMock()
    state.audit.record = AsyncMock()
    with mock_aws():
        state.s3_client = boto3.client(
            "s3",
            aws_access_key_id="ak",
            aws_secret_access_key="sk",
            region_name="us-east-1",
        )
        result = await ensure_bucket_route("new-bucket", state=state)
    assert result.bucket == "new-bucket"
    assert result.created is True


@pytest.mark.asyncio
async def test_ensure_bucket_second_call_reports_existing() -> None:
    pytest.importorskip("moto")
    import boto3
    from moto import mock_aws

    state = MagicMock()
    state.audit.record = AsyncMock()
    with mock_aws():
        state.s3_client = boto3.client(
            "s3",
            aws_access_key_id="ak",
            aws_secret_access_key="sk",
            region_name="us-east-1",
        )
        state.s3_client.create_bucket(Bucket="already-exists")
        result = await ensure_bucket_route("already-exists", state=state)
    assert result.bucket == "already-exists"
    assert result.created is False
