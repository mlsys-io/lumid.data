"""S3 / MinIO client + helpers — used by /storage and the /agent storage tools."""

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class S3Config:
    endpoint: str
    access_key: str
    secret_key: str
    region: str
    default_bucket: str


def make_client(cfg: S3Config) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        region_name=cfg.region,
    )


def compute_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def put_idempotent(
    client: Any, bucket: str, key: str, payload: bytes, content_type: str | None
) -> str:
    """Write payload to s3://bucket/key. No-op if HEAD already returns 200."""
    try:
        client.head_object(Bucket=bucket, Key=key)
        return f"s3://{bucket}/{key}"
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            raise
    extra: dict[str, str] = {"ContentType": content_type} if content_type else {}
    client.put_object(Bucket=bucket, Key=key, Body=payload, **extra)
    return f"s3://{bucket}/{key}"


def stream_get(
    client: Any, bucket: str, key: str
) -> tuple[Iterable[bytes], dict[str, Any]]:
    obj = client.get_object(Bucket=bucket, Key=key)
    body = obj["Body"]
    metadata = {
        "content_type": obj.get("ContentType"),
        "content_length": obj.get("ContentLength"),
        "etag": obj.get("ETag"),
        "last_modified": (
            obj.get("LastModified").isoformat() if obj.get("LastModified") else None
        ),
    }
    return body.iter_chunks(chunk_size=64 * 1024), metadata


def delete(client: Any, bucket: str, key: str) -> None:
    client.delete_object(Bucket=bucket, Key=key)


def list_objects(
    client: Any, bucket: str, prefix: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {"Bucket": bucket, "MaxKeys": min(limit, 1000)}
    if prefix:
        kwargs["Prefix"] = prefix
    resp = client.list_objects_v2(**kwargs)
    return [
        {
            "key": item["Key"],
            "size": int(item["Size"]),
            "etag": item.get("ETag"),
            "last_modified": (
                item["LastModified"].isoformat() if item.get("LastModified") else None
            ),
        }
        for item in resp.get("Contents", [])
    ]


def presign_put(
    client: Any, bucket: str, key: str, expires_sec: int, content_type: str | None
) -> str:
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if content_type:
        params["ContentType"] = content_type
    return client.generate_presigned_url(
        "put_object", Params=params, ExpiresIn=expires_sec
    )


def presign_get(client: Any, bucket: str, key: str, expires_sec: int) -> str:
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires_sec
    )
