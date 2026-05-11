"""Validate synchronized package versions for a lumid.data release."""

import argparse
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PYPROJECTS: tuple[Path, ...] = (
    REPO_ROOT / "pyproject.toml",
    REPO_ROOT / "sdk" / "pyproject.toml",
)
FIRST_PARTY_DISTRIBUTIONS = {
    "lumid-data",
    "lumid-data-sdk",
}

_TAG_RE = re.compile(r"^v(?P<version>[0-9]+(?:\.[0-9]+){2}[A-Za-z0-9.!+_-]*)$")
_DEP_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^\]]+\])?(?P<rest>.*)$")
_EXACT_PIN_RE = re.compile(r"^==(?P<version>[^;,\s]+)")


def _load_pyproject(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _release_version(tag: str | None) -> str | None:
    if tag is None:
        return None
    match = _TAG_RE.fullmatch(tag)
    if match is None:
        raise SystemExit(f"Release tag must look like vX.Y.Z, got {tag!r}.")
    return match.group("version")


def _project_dependencies(data: dict) -> list[str]:
    project = data.get("project", {})
    dependencies = list(project.get("dependencies", []))
    dependency_groups = data.get("dependency-groups", {})
    for specs in dependency_groups.values():
        dependencies.extend(spec for spec in specs if isinstance(spec, str))
    return dependencies


def _check_versions(expected: str | None) -> str:
    versions: dict[str, str] = {}
    for path in PACKAGE_PYPROJECTS:
        data = _load_pyproject(path)
        project = data["project"]
        name = project["name"]
        version = project["version"]
        rel = path.relative_to(REPO_ROOT)
        versions[name] = version
        if expected is not None and version != expected:
            raise SystemExit(
                f"{rel} declares {name} {version}, expected release {expected}."
            )

    unique_versions = set(versions.values())
    if len(unique_versions) != 1:
        formatted = ", ".join(
            f"{name}={version}" for name, version in sorted(versions.items())
        )
        raise SystemExit(f"Published package versions differ: {formatted}.")
    return unique_versions.pop()


def _check_internal_pins(expected: str) -> None:
    failures: list[str] = []
    for path in PACKAGE_PYPROJECTS:
        data = _load_pyproject(path)
        rel = path.relative_to(REPO_ROOT)
        for spec in _project_dependencies(data):
            match = _DEP_RE.match(spec)
            if match is None:
                continue
            name = match.group("name").lower().replace("_", "-")
            if name not in FIRST_PARTY_DISTRIBUTIONS:
                continue
            pin = _EXACT_PIN_RE.match(match.group("rest").lstrip())
            if pin is None:
                failures.append(f"{rel}: {spec!r} should pin =={expected}")
            elif pin.group("version") != expected:
                failures.append(f"{rel}: {spec!r} should pin =={expected}")
    if failures:
        raise SystemExit("Stale internal package pins:\n" + "\n".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        help="Release tag to validate, usually github.event.release.tag_name.",
    )
    args = parser.parse_args()

    expected = _release_version(args.tag)
    version = _check_versions(expected)
    _check_internal_pins(version)
    print(f"Release package versions are synchronized at {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
