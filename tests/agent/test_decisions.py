"""Routing decisions unit tests."""

import pytest

from lumid_data.agent.decisions import decide
from lumid_data.schemas.descriptors import SourceDescriptor


def _desc(cadence: str = "batch", **kwargs) -> SourceDescriptor:
    return SourceDescriptor(
        name="hello-world",
        tenant="acme",
        cadence=cadence,  # type: ignore[arg-type]
        **kwargs,
    )


def test_batch_structured_routes_to_delta() -> None:
    d = decide(_desc(), modality="structured")
    assert d.route == "delta"
    assert d.target_table == "lumid_data.acme.hello_world"


def test_batch_text_routes_to_delta() -> None:
    d = decide(_desc(), modality="text")
    assert d.route == "delta"


def test_app_namespace_in_path() -> None:
    d = decide(_desc(app="system_research"), modality="structured")
    assert d.target_table == "lumid_data.acme.system_research.hello_world"


def test_blob_routes_to_uc_volume() -> None:
    d = decide(_desc(), modality="image")
    assert d.route == "uc_volume_with_manifest"
    assert d.target_volume is not None


def test_unhandled_combination_raises() -> None:
    desc = _desc(cadence="pull")
    with pytest.raises(NotImplementedError):
        decide(desc, modality="audio")


def test_stream_cadence_routes_to_rw_stream() -> None:
    d = decide(_desc(cadence="stream"), modality="text")
    assert d.route == "rw_stream"
    assert d.target_topic is not None
