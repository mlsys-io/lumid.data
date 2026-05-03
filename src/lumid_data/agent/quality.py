"""Grounding stage (part 2): quality gates.

Phase 1 checks: row count, null ratio, duplicate ratio on declared dedup
keys. Failures are recorded in ``QualityReport`` and the caller decides
whether to drop rows, route to DLQ, or fail the ingest based on the
descriptor's policy.
"""

import logging

import pyarrow as pa

from ..schemas.descriptors import QualityReport, SourceDescriptor

logger = logging.getLogger(__name__)


def check(
    table: pa.Table, descriptor: SourceDescriptor
) -> tuple[pa.Table, QualityReport]:
    rows_in = table.num_rows
    issues: list[str] = []

    null_ratio = _null_ratio(table)
    if null_ratio > descriptor.policy.null_threshold:
        issues.append(
            f"null ratio {null_ratio:.3f} exceeds threshold "
            f"{descriptor.policy.null_threshold:.3f}"
        )

    dedup_ratio, table = _dedup(table, descriptor.dedup_keys)
    if dedup_ratio > 0.0:
        issues.append(f"dropped {dedup_ratio:.3f} of rows as duplicates")

    if descriptor.policy.require_non_empty and table.num_rows == 0:
        issues.append("payload yielded zero rows after dedup")

    rows_out = table.num_rows
    return table, QualityReport(
        rows_in=rows_in,
        rows_out=rows_out,
        rows_dropped=rows_in - rows_out,
        null_ratio=null_ratio,
        duplicate_ratio=dedup_ratio,
        schema_drift=False,
        issues=issues,
    )


def _null_ratio(table: pa.Table) -> float:
    if table.num_rows == 0 or table.num_columns == 0:
        return 0.0
    total = table.num_rows * table.num_columns
    nulls = sum(col.null_count for col in table.columns)
    return nulls / total


def _dedup(table: pa.Table, keys: list[str]) -> tuple[float, pa.Table]:
    if not keys or table.num_rows == 0:
        return 0.0, table
    missing = [k for k in keys if k not in table.column_names]
    if missing:
        logger.warning("dedup keys not present in payload, skipping: %s", missing)
        return 0.0, table
    seen: set[tuple] = set()
    keep_mask: list[bool] = []
    columns = [table.column(k) for k in keys]
    for i in range(table.num_rows):
        key = tuple(c[i].as_py() for c in columns)
        if key in seen:
            keep_mask.append(False)
        else:
            seen.add(key)
            keep_mask.append(True)
    deduped = table.filter(pa.array(keep_mask))
    ratio = (table.num_rows - deduped.num_rows) / max(table.num_rows, 1)
    return ratio, deduped
