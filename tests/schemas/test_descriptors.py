"""Pydantic schema sanity tests."""

import pytest
from pydantic import ValidationError

from lumid_data.schemas.descriptors import (
    DatasetReady,
    IngestPlan,
    QualityReport,
    SourceDescriptor,
)


def test_source_descriptor_minimal() -> None:
    d = SourceDescriptor(name="t", tenant="acme", cadence="batch")
    assert d.cadence == "batch"
    assert d.modality == "auto"
    assert d.policy.null_threshold == 0.5


def test_source_descriptor_rejects_bad_cadence() -> None:
    with pytest.raises(ValidationError):
        SourceDescriptor(name="t", tenant="acme", cadence="bogus")  # type: ignore[arg-type]


def test_quality_report_defaults_zero() -> None:
    qr = QualityReport()
    assert qr.rows_in == 0
    assert qr.issues == []


def test_ingest_plan_round_trip() -> None:
    p = IngestPlan(
        plan_id="pln-x",
        ingest_id="ing-x",
        source_id="src-x",
        received_at="2026-05-02T00:00:00+00:00",  # type: ignore[arg-type]
        modality_resolved="structured",
        route="delta",
        target_table="lumid_data.acme.t",
    )
    again = IngestPlan.model_validate_json(p.model_dump_json())
    assert again == p


def test_dataset_ready_round_trip() -> None:
    e = DatasetReady(
        event_id="evt-x",
        dataset_id="dst-x",
        source_id="src-x",
        modality="text",
        is_first=True,
    )
    again = DatasetReady.model_validate_json(e.model_dump_json())
    assert again.dataset_id == "dst-x"
