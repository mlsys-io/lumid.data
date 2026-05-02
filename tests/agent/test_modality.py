"""Modality classifier unit tests."""

from lumid_data.agent.modality import classify
from lumid_data.schemas.descriptors import SourceDescriptor


def _desc(modality: str = "auto", mime_hint: str | None = None) -> SourceDescriptor:
    return SourceDescriptor(
        name="t",
        tenant="acme",
        cadence="batch",
        modality=modality,  # type: ignore[arg-type]
        mime_hint=mime_hint,
    )


def test_descriptor_override_wins() -> None:
    assert classify(b"anything", _desc(modality="text")) == "text"


def test_csv_detect_by_mime() -> None:
    assert classify(b"a,b,c\n1,2,3\n", _desc(mime_hint="text/csv")) == "structured"


def test_json_array_detect_by_content() -> None:
    payload = b'[{"a": 1}, {"a": 2}]'
    assert classify(payload, _desc()) == "structured"


def test_csv_detect_by_content() -> None:
    assert classify(b"x,y\n1,2\n3,4\n", _desc()) == "structured"


def test_text_detect_by_content() -> None:
    assert classify(b"This is just some plain text content.", _desc()) == "text"


def test_image_detect_by_mime() -> None:
    assert classify(b"\x89PNG\r\n", _desc(mime_hint="image/png")) == "image"
