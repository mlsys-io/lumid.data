"""S3 prefix watcher pull connector.

List objects under a prefix; download those newer than the last seen
checkpoint and submit them to ingest. Checkpointing is by ``LastModified``;
the caller persists the cursor (e.g., in the descriptor's pull_config
options) and passes it back on next call.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import boto3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class S3WatchSpec:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    prefix: str
    region: str = "us-east-1"


def list_new_objects(
    spec: S3WatchSpec,
    since: datetime | None,
    max_keys: int = 1000,
) -> list[dict[str, Any]]:
    s3 = boto3.client(
        "s3",
        endpoint_url=spec.endpoint_url,
        aws_access_key_id=spec.access_key,
        aws_secret_access_key=spec.secret_key,
        region_name=spec.region,
    )
    paginator = s3.get_paginator("list_objects_v2")
    new: list[dict[str, Any]] = []
    for page in paginator.paginate(
        Bucket=spec.bucket, Prefix=spec.prefix, PaginationConfig={"PageSize": 1000}
    ):
        for obj in page.get("Contents", []):
            mod = obj["LastModified"]
            if since is not None and mod <= since:
                continue
            new.append(
                {
                    "key": obj["Key"],
                    "size": int(obj["Size"]),
                    "last_modified": mod,
                    "etag": obj.get("ETag"),
                }
            )
            if len(new) >= max_keys:
                return new
    return new


def fetch(spec: S3WatchSpec, key: str) -> bytes:
    s3 = boto3.client(
        "s3",
        endpoint_url=spec.endpoint_url,
        aws_access_key_id=spec.access_key,
        aws_secret_access_key=spec.secret_key,
        region_name=spec.region,
    )
    r = s3.get_object(Bucket=spec.bucket, Key=key)
    return bytes(r["Body"].read())
