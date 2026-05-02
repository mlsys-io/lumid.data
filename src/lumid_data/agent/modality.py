"""Perception stage: modality classifier.

Resolves a payload's modality from MIME / magic-byte sniff plus the
descriptor's optional override. Defaults to ``"structured"`` when the
sniff is ambiguous and the payload looks tabular (CSV / JSON array /
Parquet), otherwise ``"text"`` for printable bytes.
"""

import logging

from ..schemas.descriptors import Modality, SourceDescriptor

logger = logging.getLogger(__name__)

_STRUCTURED_MIMES = {
    "text/csv",
    "application/csv",
    "application/json",
    "application/x-ndjson",
    "application/parquet",
    "application/x-parquet",
    "application/vnd.apache.parquet",
}
_TEXT_MIMES = {"text/plain", "text/markdown", "text/x-markdown"}
_IMAGE_PREFIX = "image/"
_AUDIO_PREFIX = "audio/"
_VIDEO_PREFIX = "video/"


def classify(
    payload: bytes,
    descriptor: SourceDescriptor,
    mime_override: str | None = None,
) -> Modality:
    if descriptor.modality != "auto":
        return descriptor.modality

    mime = (mime_override or descriptor.mime_hint or "").lower().split(";", 1)[0].strip()
    if mime:
        if mime in _STRUCTURED_MIMES:
            return "structured"
        if mime in _TEXT_MIMES:
            return "text"
        if mime.startswith(_IMAGE_PREFIX):
            return "image"
        if mime.startswith(_AUDIO_PREFIX):
            return "audio"
        if mime.startswith(_VIDEO_PREFIX):
            return "video"

    sniff = _magic_sniff(payload)
    if sniff:
        return sniff

    if _looks_like_json_array(payload) or _looks_like_csv(payload):
        return "structured"
    if _looks_like_text(payload):
        return "text"
    return "blob"


def _magic_sniff(payload: bytes) -> Modality | None:
    try:
        import magic
    except ImportError:
        logger.debug("python-magic not available; falling back to heuristics")
        return None
    try:
        detected = magic.from_buffer(payload[:8192], mime=True) or ""
    except Exception:
        return None
    detected = detected.lower()
    if detected in _STRUCTURED_MIMES:
        return "structured"
    if detected in _TEXT_MIMES or detected == "text/plain":
        return "text"
    if detected.startswith(_IMAGE_PREFIX):
        return "image"
    if detected.startswith(_AUDIO_PREFIX):
        return "audio"
    if detected.startswith(_VIDEO_PREFIX):
        return "video"
    return None


def _looks_like_json_array(payload: bytes) -> bool:
    head = payload[:64].lstrip()
    return head.startswith(b"[") or head.startswith(b"{")


def _looks_like_csv(payload: bytes) -> bool:
    head = payload[:8192]
    if b"," not in head:
        return False
    try:
        text = head.decode("utf-8", errors="ignore")
    except Exception:
        return False
    lines = [line for line in text.splitlines() if line.strip()][:5]
    if len(lines) < 2:
        return False
    first_commas = lines[0].count(",")
    return first_commas >= 1 and all(line.count(",") == first_commas for line in lines[1:])


def _looks_like_text(payload: bytes) -> bool:
    head = payload[:1024]
    if not head:
        return False
    printable = sum(1 for b in head if 32 <= b < 127 or b in (9, 10, 13))
    return printable / max(len(head), 1) > 0.85
