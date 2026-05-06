"""MinIO-backed cache for :class:`SchemaCardBundle`.

Cards are stored as JSON at
``s3://<bucket>/lumid-data-meta/schema-cards/<scope-hash>.json`` with a TTL.
Lookup is keyed on a sha256 of the (normalized) scope string so the
key is stable across calls but doesn't leak schema names into the URL.

The cache is a thin orchestration layer around :class:`SchemaCardBuilder`
and the existing s3 service: ``get(scope)`` returns the cached bundle if
fresh, otherwise rebuilds-and-stores. ``refresh(scope)`` forces a rebuild.
"""

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from botocore.exceptions import ClientError

from .schema_card import SchemaCardBundle


class _BundleBuilder(Protocol):
    """Just the surface ``SchemaCardCache`` actually invokes."""

    async def build(self, scope: str) -> SchemaCardBundle: ...


logger = logging.getLogger(__name__)

_CACHE_PREFIX = "lumid-data-meta/schema-cards"
_DEFAULT_TTL = timedelta(hours=24)


class SchemaCardCache:
    """Get-or-build cache for schema card bundles.

    The bundle is the unit of caching — one cache entry per scope spec
    string. Different scope specs (e.g. ``"star.*"`` vs an explicit
    ``"star.x,star.y"``) are independent entries so callers don't pay
    for cards they didn't ask for.
    """

    def __init__(
        self,
        *,
        builder: _BundleBuilder,
        s3_client: Any,
        bucket: str,
        ttl: timedelta | None = None,
    ) -> None:
        self._builder = builder
        self._s3 = s3_client
        self._bucket = bucket
        self._ttl = ttl or _DEFAULT_TTL

    async def get(self, scope: str) -> SchemaCardBundle:
        cached = await self._load(scope)
        if cached is not None and self._is_fresh(cached):
            return cached
        return await self._rebuild_and_save(scope)

    async def refresh(self, scope: str) -> SchemaCardBundle:
        return await self._rebuild_and_save(scope)

    async def _rebuild_and_save(self, scope: str) -> SchemaCardBundle:
        bundle = await self._builder.build(scope)
        await self._save(scope, bundle)
        return bundle

    async def _load(self, scope: str) -> SchemaCardBundle | None:
        key = _key(scope)
        try:
            payload = await asyncio.to_thread(self._get_object_bytes, key)
        except ClientError as exc:
            if _is_not_found(exc):
                return None
            raise
        try:
            return SchemaCardBundle.model_validate_json(payload)
        except Exception:
            logger.warning("cached schema bundle invalid; ignoring at %s", key)
            return None

    async def _save(self, scope: str, bundle: SchemaCardBundle) -> None:
        key = _key(scope)
        body = bundle.model_dump_json(indent=2).encode()
        await asyncio.to_thread(self._put_object, key, body)

    def _is_fresh(self, bundle: SchemaCardBundle) -> bool:
        oldest = (
            min((c.built_at for c in bundle.tables), default=None)
            if bundle.tables
            else None
        )
        if oldest is None:
            return False
        return (datetime.now(UTC) - oldest) < self._ttl

    def _put_object(self, key: str, body: bytes) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )

    def _get_object_bytes(self, key: str) -> bytes:
        obj = self._s3.get_object(Bucket=self._bucket, Key=key)
        return obj["Body"].read()


def _key(scope: str) -> str:
    """Stable key for a scope spec.

    The scope string is normalized (whitespace stripped, parts sorted)
    before hashing so semantically-equal specs (``"a, b"`` vs ``"b,a"``)
    share a cache entry.
    """
    parts = sorted(p.strip() for p in scope.split(",") if p.strip())
    normalized = ",".join(parts)
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:32]
    return f"{_CACHE_PREFIX}/{digest}.json"


def _is_not_found(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code", "")
    return code in {"404", "NoSuchKey", "NotFound"}
