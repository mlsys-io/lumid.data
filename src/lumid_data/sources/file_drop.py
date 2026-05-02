"""Local-filesystem watcher pull connector.

For each file under the configured path that hasn't been seen before,
read it and submit it to the data plane's ingest endpoint. Idempotency
is by sha256 of the content (the agent's quality stage drops dupes).
"""

import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


def discover(path: str, glob: str = "*") -> Iterator[Path]:
    """Yield files under ``path`` matching ``glob`` in deterministic order."""
    base = Path(path)
    if not base.exists():
        return
    for p in sorted(base.glob(glob)):
        if p.is_file():
            yield p


def read_batch(
    path: str,
    glob: str = "*",
    max_files: int = 100,
) -> list[tuple[Path, bytes]]:
    """Read up to ``max_files`` files; returns (path, payload) pairs."""
    out: list[tuple[Path, bytes]] = []
    for p in discover(path, glob):
        if len(out) >= max_files:
            break
        out.append((p, p.read_bytes()))
    return out
