"""Update synchronized lumid.data package versions and internal pins."""

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PYPROJECTS: tuple[Path, ...] = (
    REPO_ROOT / "pyproject.toml",
    REPO_ROOT / "sdk" / "pyproject.toml",
)
FIRST_PARTY_DISTRIBUTIONS: tuple[str, ...] = (
    "lumid-data-sdk",
    "lumid-data",
)

_VERSION_RE = re.compile(r'(?m)^version = "[^"]+"$')
_PIN_RE = re.compile(
    r"(?P<name>\b(?:"
    + "|".join(re.escape(name) for name in FIRST_PARTY_DISTRIBUTIONS)
    + r")\b)(?P<extras>\[[^\]]+\])?==(?P<version>[^\"'\s,\]]+)"
)
_VERSION_VALUE_RE = re.compile(r"^v?[0-9]+(?:\.[0-9]+){2}[A-Za-z0-9.!+_-]*$")


def _normalize_version(raw: str) -> str:
    if _VERSION_VALUE_RE.fullmatch(raw) is None:
        raise SystemExit(f"Version must look like X.Y.Z or vX.Y.Z, got {raw!r}.")
    return raw.removeprefix("v")


def _render(text: str, version: str, path: Path) -> str:
    text, count = _VERSION_RE.subn(f'version = "{version}"', text, count=1)
    if count != 1:
        rel = path.relative_to(REPO_ROOT)
        raise SystemExit(f"Expected one project version line in {rel}.")
    return _PIN_RE.sub(
        lambda match: (
            f"{match.group('name')}{match.group('extras') or ''}=={version}"
        ),
        text,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Target package version, for example 0.1.1.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if package files are not already set to the requested version.",
    )
    args = parser.parse_args()

    version = _normalize_version(args.version)
    changed: list[Path] = []
    for path in PACKAGE_PYPROJECTS:
        original = path.read_text()
        rendered = _render(original, version, path)
        if rendered != original:
            changed.append(path)
            if not args.check:
                path.write_text(rendered)

    if changed and args.check:
        formatted = "\n".join(str(path.relative_to(REPO_ROOT)) for path in changed)
        raise SystemExit(f"Package files are not set to {version}:\n{formatted}")
    if changed:
        for path in changed:
            print(f"updated {path.relative_to(REPO_ROOT)}")
    else:
        print(f"Package versions are already set to {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
