"""lumid.data client core.

Sync and async surfaces; sync is what app commands typically import,
async is exposed for callers that already run an event loop.
"""

import json
import logging
import os
import tomllib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import nats
import pyarrow as pa
from deltalake import DeltaTable

from ..catalog.nats_publisher import SUBJECT_DATASET_READY, SUBJECT_LINEAGE_RUN

logger = logging.getLogger(__name__)


class ClientError(RuntimeError):
    """Raised when a data-plane call fails after the network layer."""


@dataclass(frozen=True)
class Credentials:
    """Typed view over ``~/.lumid/apps/<app>/credentials.toml``."""

    app: str
    raw: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def required(self, key: str) -> str:
        value = self.raw.get(key)
        if value is None:
            raise KeyError(f"missing required credential {key!r} in app {self.app!r}")
        return str(value)


class Client:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        nats_url: str | None = None,
        s3_endpoint: str | None = None,
        s3_access_key: str | None = None,
        s3_secret_key: str | None = None,
        s3_region: str = "us-east-1",
        timeout_sec: float = 30.0,
    ) -> None:
        self._base_url = (base_url or os.environ["LUMID_DATA_URL"]).rstrip("/")
        self._token = token or os.environ.get("LUMID_TOKEN")
        self._nats_url = nats_url or os.environ.get("NATS_URL")
        self._s3_endpoint = s3_endpoint or os.environ.get("S3_ENDPOINT")
        self._s3_access_key = s3_access_key or os.environ.get("S3_ACCESS_KEY")
        self._s3_secret_key = s3_secret_key or os.environ.get("S3_SECRET_KEY")
        self._s3_region = s3_region
        self._timeout = httpx.Timeout(timeout_sec, connect=5.0)

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def ingest(
        self,
        source_id: str,
        payload: bytes | str | dict[str, Any] | list[dict[str, Any]],
        *,
        mime: str | None = None,
    ) -> dict[str, Any]:
        body, default_mime = _encode_payload(payload)
        headers = {**self._headers(), "Content-Type": mime or default_mime}
        with httpx.Client(timeout=self._timeout) as c:
            r = c.post(
                f"{self._base_url}/v1/ingest/{source_id}",
                content=body,
                headers=headers,
            )
            if r.status_code >= 300:
                raise ClientError(f"ingest failed ({r.status_code}): {r.text}")
            return r.json()

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(f"{self._base_url}/v1/datasets/{dataset_id}", headers=self._headers())
            if r.status_code >= 300:
                raise ClientError(f"dataset fetch failed ({r.status_code}): {r.text}")
            return r.json()

    def list_datasets(self, source_id: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if source_id:
            params["source_id"] = source_id
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(
                f"{self._base_url}/v1/datasets",
                headers=self._headers(),
                params=params,
            )
            if r.status_code >= 300:
                raise ClientError(f"datasets list failed ({r.status_code}): {r.text}")
            return r.json()

    def read_table(self, dataset_id: str) -> pa.Table:
        meta = self.get_dataset(dataset_id)
        uri = meta.get("table_uri")
        if not uri:
            raise ClientError(f"dataset {dataset_id} has no table_uri")
        return self._read_delta_uri(uri)

    def preview(self, dataset_id: str, n: int = 20) -> pa.Table:
        table = self.read_table(dataset_id)
        return table.slice(0, n)

    def _read_delta_uri(self, uri: str) -> pa.Table:
        if not self._s3_endpoint or not self._s3_access_key or not self._s3_secret_key:
            raise ClientError("S3 endpoint/credentials not configured for read_table")
        storage_options = {
            "AWS_ACCESS_KEY_ID": self._s3_access_key,
            "AWS_SECRET_ACCESS_KEY": self._s3_secret_key,
            "AWS_ENDPOINT_URL": self._s3_endpoint,
            "AWS_REGION": self._s3_region,
            "AWS_ALLOW_HTTP": "true",
        }
        dt = DeltaTable(uri, storage_options=storage_options)
        return dt.to_pyarrow_table()

    def creds(self, app: str) -> Credentials:
        path = Path.home() / ".lumid" / "apps" / app / "credentials.toml"
        if not path.exists():
            return Credentials(app=app, raw={})
        with path.open("rb") as f:
            data = tomllib.load(f)
        return Credentials(app=app, raw=data)

    async def subscribe(self, event: str = "DatasetReady") -> AsyncIterator[dict[str, Any]]:
        if self._nats_url is None:
            raise ClientError("NATS_URL not configured for subscribe()")
        subject = {
            "DatasetReady": SUBJECT_DATASET_READY,
            "Lineage": SUBJECT_LINEAGE_RUN,
        }.get(event)
        if subject is None:
            raise ClientError(f"unknown event {event!r}")
        nc = await nats.connect(self._nats_url)
        sub = await nc.subscribe(subject)
        try:
            async for msg in sub.messages:
                yield json.loads(msg.data.decode("utf-8"))
        finally:
            await sub.unsubscribe()
            await nc.drain()


def _encode_payload(
    payload: bytes | str | dict[str, Any] | list[dict[str, Any]],
) -> tuple[bytes, str]:
    if isinstance(payload, bytes):
        return payload, "application/octet-stream"
    if isinstance(payload, str):
        return payload.encode("utf-8"), "text/plain"
    if isinstance(payload, list) or isinstance(payload, dict):
        return json.dumps(payload).encode("utf-8"), "application/json"
    raise TypeError(f"unsupported payload type {type(payload).__name__}")
