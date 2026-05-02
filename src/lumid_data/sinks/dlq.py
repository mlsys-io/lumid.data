"""Failed-batch sink. Records the DLQ row in Postgres and stores the
payload bytes to MinIO under a deterministic key. The payload remains
fetchable for ``lumid-data dlq replay <id>``.
"""

import logging
from dataclasses import dataclass

import boto3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DlqSinkConfig:
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"


def store_payload(cfg: DlqSinkConfig, dlq_id: str, payload: bytes, mime: str | None) -> str:
    """Store the raw payload to MinIO; return the s3 URI."""
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
        region_name=cfg.s3_region,
    )
    key = f"dlq/{dlq_id}.bin"
    extra: dict[str, str] = {}
    if mime:
        extra["ContentType"] = mime
    s3.put_object(Bucket=cfg.s3_bucket, Key=key, Body=payload, **extra)
    return f"s3://{cfg.s3_bucket}/{key}"
