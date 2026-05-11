"""Validate built lumid.data distributions."""

import argparse
import subprocess
import tempfile
import venv
from pathlib import Path


def _script_bin(env_dir: Path, name: str) -> Path:
    return env_dir / "bin" / name


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)  # nosec B603: fixed argv list, no shell.


def _wheel(dist_dir: Path, prefix: str) -> Path:
    wheels = sorted(dist_dir.glob(f"{prefix}-*-py3-none-any.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected one {prefix} wheel, found {len(wheels)}")
    return wheels[0]


def _create_venv(env_dir: Path) -> Path:
    venv.EnvBuilder(with_pip=True).create(env_dir)
    return _script_bin(env_dir, "python")


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


def _smoke_root(dist_dir: Path, root_wheel: Path) -> None:
    """Install the root wheel with sibling distributions and run the CLI."""
    code = """
from lumid_data.sdk import Client
import lumid_data.server.main

assert Client
"""
    with tempfile.TemporaryDirectory(prefix="lumid-data-smoke-") as tmp:
        env_dir = Path(tmp) / ".venv"
        python = _create_venv(env_dir)
        _run(
            [
                python.as_posix(),
                "-m",
                "pip",
                "install",
                "--find-links",
                dist_dir.as_posix(),
                root_wheel.as_posix(),
            ]
        )
        _run([python.as_posix(), "-c", code])
        _run([_script_bin(env_dir, "lumid-data").as_posix(), "--help"])


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

    sdk_wheel = _wheel(dist_dir, "lumid_data_sdk")
    root_wheel = _wheel(dist_dir, "lumid_data")
    _smoke_sdk(sdk_wheel)
    _smoke_root(dist_dir, root_wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
