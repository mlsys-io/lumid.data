"""Grounding stage (part 1): schema inference + fingerprint.

For Phase 1 we handle:

- ``structured``: CSV / NDJSON / JSON-array → pyarrow.Table; emit schema
  + fingerprint.
- ``text``: a fixed schema ``(content STRING, mime STRING, ts TIMESTAMP)``;
  the caller wraps the payload before passing to this module.

The schema fingerprint is a stable hash of the field list (name, type)
and is used to detect schema drift against the existing dataset version.
"""

import csv
import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa

from ..schemas.descriptors import Modality

logger = logging.getLogger(__name__)


def infer(payload: bytes, modality: Modality, mime_hint: str | None = None) -> pa.Table:
    if modality == "structured":
        return _infer_structured(payload, mime_hint)
    if modality == "text":
        return _wrap_text(payload, mime_hint)
    raise ValueError(f"Phase 1 schema inference does not support modality={modality!r}")


def fingerprint(schema: pa.Schema) -> str:
    parts = [f"{f.name}:{f.type}" for f in schema]
    digest = hashlib.sha256("|".join(parts).encode("utf-8"))
    return digest.hexdigest()[:16]


def reconcile(inferred: pa.Schema, existing: pa.Schema | None) -> tuple[bool, list[str]]:
    """Return (is_drift, issues). Additive-only is the default; caller decides what to do."""
    if existing is None:
        return False, []
    inferred_fields = {f.name: f.type for f in inferred}
    existing_fields = {f.name: f.type for f in existing}
    issues: list[str] = []
    for name, t in existing_fields.items():
        if name not in inferred_fields:
            issues.append(f"missing field in payload: {name}")
        elif inferred_fields[name] != t:
            issues.append(
                f"type mismatch on {name}: existing={t!r} payload={inferred_fields[name]!r}"
            )
    new_fields = set(inferred_fields) - set(existing_fields)
    if new_fields:
        issues.append(f"new fields (additive): {sorted(new_fields)}")
    is_drift = bool(issues)
    return is_drift, issues


def _infer_structured(payload: bytes, mime_hint: str | None) -> pa.Table:
    mime = (mime_hint or "").lower()
    if mime in {"application/parquet", "application/x-parquet", "application/vnd.apache.parquet"}:
        return _parse_parquet(payload)
    if mime in {"application/json"}:
        return _parse_json_array(payload)
    if mime in {"application/x-ndjson"}:
        return _parse_ndjson(payload)
    if mime in {"text/csv", "application/csv"}:
        return _parse_csv(payload)
    head = payload[:8192].lstrip()
    if head.startswith(b"PAR1") or head.startswith(b"\x50\x41\x52\x31"):
        return _parse_parquet(payload)
    if head.startswith(b"["):
        return _parse_json_array(payload)
    if head.startswith(b"{"):
        return _parse_ndjson(payload)
    return _parse_csv(payload)


def _parse_parquet(payload: bytes) -> pa.Table:
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(payload))


def _parse_json_array(payload: bytes) -> pa.Table:
    rows: Any = json.loads(payload)
    if not isinstance(rows, list):
        raise ValueError("JSON payload must be an array of objects")
    if not rows:
        return pa.Table.from_pylist([])
    return pa.Table.from_pylist(rows)


def _parse_ndjson(payload: bytes) -> pa.Table:
    rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
    return pa.Table.from_pylist(rows)


def _parse_csv(payload: bytes) -> pa.Table:
    text = payload.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return pa.Table.from_pylist(rows)


def _wrap_text(payload: bytes, mime_hint: str | None) -> pa.Table:
    rows = [
        {
            "content": payload.decode("utf-8", errors="replace"),
            "mime": (mime_hint or "text/plain"),
            "ts": datetime.now(timezone.utc),
        }
    ]
    return pa.Table.from_pylist(rows)
