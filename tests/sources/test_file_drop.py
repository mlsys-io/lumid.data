"""File-drop pull connector tests."""

from pathlib import Path

from lumid_data.sources.file_drop import discover, read_batch


def test_discover_yields_files_in_order(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "a.txt").write_text("a")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("c")
    found = [p.name for p in discover(str(tmp_path))]
    assert found == ["a.txt", "b.txt"]


def test_discover_glob_filter(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.json").write_text("{}")
    found = [p.name for p in discover(str(tmp_path), glob="*.json")]
    assert found == ["b.json"]


def test_read_batch_caps_files(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text(str(i))
    batch = read_batch(str(tmp_path), max_files=3)
    assert len(batch) == 3
    paths, payloads = zip(*batch, strict=True)
    assert all(p.exists() for p in paths)
    assert all(b for b in payloads)


def test_discover_missing_path_yields_nothing() -> None:
    assert list(discover("/no/such/dir/that/exists")) == []
