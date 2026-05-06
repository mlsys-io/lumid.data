"""Tests for the schema-card MinIO cache.

S3 is mocked; the cache's job is just to serialize/deserialize
:class:`SchemaCardBundle` correctly, hash scope strings into stable keys,
and decide freshness from the embedded ``built_at`` timestamps.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError

from lumid_data.retrieve.card_store import SchemaCardCache, _key
from lumid_data.retrieve.schema_card import (
    ColumnCard,
    SchemaCard,
    SchemaCardBundle,
)


class _FakeS3:
    """Tiny fake of the boto3 S3 client surface used by the cache."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str, bytes]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

        class _Body:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def read(self) -> bytes:
                return self._data

        return {"Body": _Body(self.objects[(Bucket, Key)])}

    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str
    ) -> None:
        self.objects[(Bucket, Key)] = Body
        self.put_calls.append((Bucket, Key, Body))


class _FakeBuilder:
    """Returns a stub bundle, recording how many times build() was invoked."""

    def __init__(self, freshness: timedelta = timedelta(0)) -> None:
        self._age = freshness
        self.build_count = 0

    async def build(self, scope: str) -> SchemaCardBundle:
        self.build_count += 1
        built_at = datetime.now(UTC) - self._age
        card = SchemaCard(
            fqname="schema.t",
            columns=[ColumnCard(name="id", type="int", nullable=False)],
            built_at=built_at,
        )
        return SchemaCardBundle(scope=scope, tables=[card])


class TestKey:
    def test_hash_is_stable_for_same_input(self):
        assert _key("a.b,c.d") == _key("a.b,c.d")

    def test_normalizes_whitespace(self):
        assert _key("a.b, c.d") == _key("a.b,c.d")

    def test_is_order_insensitive(self):
        assert _key("a.b,c.d") == _key("c.d,a.b")

    def test_distinct_for_different_inputs(self):
        assert _key("a.b") != _key("a.c")

    def test_key_starts_with_prefix(self):
        assert _key("a.b").startswith("lumid-data-meta/schema-cards/")

    def test_key_does_not_leak_schema_names(self):
        assert "a.b" not in _key("a.b")


class TestSchemaCardCache:
    @pytest.fixture
    def s3(self) -> _FakeS3:
        return _FakeS3()

    @pytest.mark.asyncio
    async def test_get_builds_on_cache_miss(self, s3: _FakeS3):
        builder = _FakeBuilder()
        cache = SchemaCardCache(builder=builder, s3_client=s3, bucket="b")
        bundle = await cache.get("schema.t")
        assert builder.build_count == 1
        assert bundle.scope == "schema.t"
        assert len(s3.put_calls) == 1

    @pytest.mark.asyncio
    async def test_get_serves_from_cache_when_fresh(self, s3: _FakeS3):
        builder = _FakeBuilder()
        cache = SchemaCardCache(builder=builder, s3_client=s3, bucket="b")
        await cache.get("schema.t")
        await cache.get("schema.t")  # second call — cache hit
        assert builder.build_count == 1

    @pytest.mark.asyncio
    async def test_get_rebuilds_on_stale_entry(self, s3: _FakeS3):
        # First builder seeds a stale entry (25 hours old).
        seed_builder = _FakeBuilder(freshness=timedelta(hours=25))
        seed = SchemaCardCache(
            builder=seed_builder,
            s3_client=s3,
            bucket="b",
            ttl=timedelta(hours=24),
        )
        await seed.get("schema.t")
        assert seed_builder.build_count == 1

        # New cache instance with a fresh builder — should detect stale + rebuild.
        fresh_builder = _FakeBuilder()
        fresh = SchemaCardCache(
            builder=fresh_builder,
            s3_client=s3,
            bucket="b",
            ttl=timedelta(hours=24),
        )
        await fresh.get("schema.t")
        assert fresh_builder.build_count == 1

    @pytest.mark.asyncio
    async def test_refresh_forces_rebuild(self, s3: _FakeS3):
        builder = _FakeBuilder()
        cache = SchemaCardCache(builder=builder, s3_client=s3, bucket="b")
        await cache.get("schema.t")
        await cache.refresh("schema.t")
        assert builder.build_count == 2

    @pytest.mark.asyncio
    async def test_corrupt_cache_entry_falls_back_to_rebuild(self, s3: _FakeS3):
        builder = _FakeBuilder()
        cache = SchemaCardCache(builder=builder, s3_client=s3, bucket="b")
        # Plant garbage at the expected key
        s3.objects[("b", _key("schema.t"))] = b"not-json"
        bundle = await cache.get("schema.t")
        assert builder.build_count == 1
        assert bundle.scope == "schema.t"
