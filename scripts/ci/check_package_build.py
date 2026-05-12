"""Validate the built lumid-data-sdk distribution."""

import argparse
import subprocess
import tempfile
import venv
from pathlib import Path


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)  # nosec B603: fixed argv list, no shell.


def _sdk_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("lumid_data_sdk-*-py3-none-any.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected one lumid_data_sdk wheel, found {len(wheels)}")
    return wheels[0]


def _create_venv(env_dir: Path) -> Path:
    venv.EnvBuilder(with_pip=True).create(env_dir)
    return env_dir / "bin" / "python"


def _smoke_sdk(sdk_wheel: Path) -> None:
    """Install the SDK wheel in a fresh venv and import its public client."""
    code = """
from lumid_data.sdk import AsyncClient, Client

assert Client and AsyncClient
"""
    with tempfile.TemporaryDirectory(prefix="lumid-data-sdk-smoke-") as tmp:
        env_dir = Path(tmp) / ".venv"
        python = _create_venv(env_dir)
        _run([python.as_posix(), "-m", "pip", "install", sdk_wheel.as_posix()])
        _run([python.as_posix(), "-c", code])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist",
        default="dist",
        type=Path,
        help="Directory containing distributions built by `uv build`.",
    )
    args = parser.parse_args()

    dist_dir = args.dist.resolve()
    if not dist_dir.is_dir():
        raise SystemExit(f"Distribution directory does not exist: {dist_dir}")

    _smoke_sdk(_sdk_wheel(dist_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
