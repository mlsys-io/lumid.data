"""Tests for the registration-time stream plan."""

import pytest

from lumid_data.agent import plan_for_stream_source
from lumid_data.schemas.descriptors import SourceDescriptor


def _stream(**kwargs) -> SourceDescriptor:
    return SourceDescriptor(
        source_id="src-stream",
        name="news_ticker",
        tenant="acme",
        cadence="stream",
        **kwargs,
    )


def test_stream_plan_emits_topic_and_table() -> None:
    plan = plan_for_stream_source(_stream())
    assert plan.route == "rw_stream"
    assert plan.target_topic == "lumid_data__acme__news_ticker"
    assert plan.target_table == "lumid_data.acme.news_ticker"


def test_stream_plan_resolves_auto_modality_to_structured() -> None:
    plan = plan_for_stream_source(_stream(modality="auto"))
    assert plan.modality_resolved == "structured"


def test_stream_plan_rejects_non_stream_cadence() -> None:
    desc = SourceDescriptor(name="t", tenant="acme", cadence="batch")
    with pytest.raises(ValueError):
        plan_for_stream_source(desc)
