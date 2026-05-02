"""Tests for the stream-source bootstrap orchestrator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lumid_data.db.models import Source
from lumid_data.schemas.descriptors import SourceDescriptor
from lumid_data.server.services.streaming import bootstrap_stream_source
from lumid_data.sinks.delta import DeltaSinkConfig
from lumid_data.sinks.risingwave import RisingWaveConfig


def _state(streaming: bool = True) -> MagicMock:
    s = MagicMock()
    s.settings.uc_catalog = "lumid_data"
    s.settings.redpanda_brokers = "redpanda:9092"
    s.delta_cfg = DeltaSinkConfig(
        s3_endpoint="http://minio:9000",
        s3_access_key="ak",
        s3_secret_key="sk",
        s3_bucket="lumid-data",
    )
    s.unity = MagicMock()
    s.traces = MagicMock()
    s.nats = MagicMock()
    s.nats.publish_dataset_ready = AsyncMock()
    if streaming:
        s.redpanda = MagicMock()
        s.risingwave_cfg = RisingWaveConfig(dsn="postgresql://rw")
        s.streaming_enabled = True
    else:
        s.redpanda = None
        s.risingwave_cfg = None
        s.streaming_enabled = False
    return s


def _descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        source_id="src-stream",
        name="news",
        tenant="acme",
        cadence="stream",
    )


def _source_row() -> Source:
    return Source(
        source_id="src-stream",
        tenant="acme",
        app=None,
        name="news",
        cadence="stream",
        modality="auto",
        partition_by=[],
        dedup_keys=[],
        policy={},
    )


@pytest.mark.asyncio
async def test_bootstrap_emits_topic_uc_register_and_dataset_ready() -> None:
    state = _state()
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    with patch("lumid_data.server.services.streaming.ensure_stream_pipeline") as ensure:
        result = await bootstrap_stream_source(
            state, _descriptor(), _source_row(), session
        )

    state.redpanda.ensure_topic.assert_called_once()
    state.unity.ensure_catalog.assert_called_once_with("lumid_data")
    state.unity.ensure_schema.assert_called_once()
    state.unity.register_external_delta.assert_called_once()
    ensure.assert_called_once()
    state.nats.publish_dataset_ready.assert_awaited_once()
    assert "topic" in result and "table_uri" in result and "dataset_id" in result


@pytest.mark.asyncio
async def test_bootstrap_503_when_streaming_disabled() -> None:
    state = _state(streaming=False)
    session = MagicMock()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await bootstrap_stream_source(state, _descriptor(), _source_row(), session)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_bootstrap_400_when_cadence_not_stream() -> None:
    state = _state()
    session = MagicMock()
    desc = SourceDescriptor(name="x", tenant="acme", cadence="batch")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await bootstrap_stream_source(state, desc, _source_row(), session)
    assert exc.value.status_code == 400
