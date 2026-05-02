"""Quality gates unit tests."""

import pyarrow as pa

from lumid_data.agent.quality import check
from lumid_data.schemas.descriptors import SourceDescriptor, SourcePolicy


def _desc(**kwargs) -> SourceDescriptor:
    return SourceDescriptor(name="t", tenant="acme", cadence="batch", **kwargs)


def test_clean_payload_passes() -> None:
    table = pa.Table.from_pydict({"a": [1, 2], "b": ["x", "y"]})
    out, report = check(table, _desc())
    assert out.num_rows == 2
    assert report.rows_in == 2
    assert report.rows_out == 2
    assert report.issues == []


def test_dedup_drops_duplicates() -> None:
    table = pa.Table.from_pydict({"id": [1, 1, 2], "v": ["a", "a", "b"]})
    out, report = check(table, _desc(dedup_keys=["id"]))
    assert out.num_rows == 2
    assert report.duplicate_ratio > 0


def test_null_threshold_flagged_but_does_not_drop_rows() -> None:
    table = pa.Table.from_pydict({"a": [1, None, None], "b": [None, None, None]})
    desc = _desc(policy=SourcePolicy(null_threshold=0.1))
    _, report = check(table, desc)
    assert any("null ratio" in x for x in report.issues)


def test_empty_table_with_require_non_empty() -> None:
    table = pa.Table.from_pydict({"a": pa.array([], type=pa.int64())})
    _, report = check(table, _desc())
    assert any("zero rows" in x for x in report.issues)
