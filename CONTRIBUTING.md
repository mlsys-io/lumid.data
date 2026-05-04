# Contributing to lumid.data

## Setup

```bash
pip install uv
uv sync --all-extras --group dev
uv run pre-commit install --install-hooks -t pre-commit -t prepare-commit-msg -t commit-msg
```

## Before every commit

```bash
uv run pre-commit run --all-files
```

This runs gitleaks, isort, black, ruff, codespell, mypy, sync_requirements,
and check_env_examples. Fix pre-existing failures rather than suppressing —
do not bypass with `--no-verify` unless explicitly authorized.

## Tests

```bash
uv run pytest tests/
```

## Pull Requests

PR title format: `type(scope): description`. Allowed types:
`feat, fix, refactor, chore, test, perf, build, ci, docs`. Prefix with
`[BREAKING]` for breaking changes.

Sign off every commit under the Developer Certificate of Origin
(`git commit -s` or the prepare-commit-msg hook installed above).

Disclose AI assistance via a commit trailer:
`Co-Authored-By: <agent name> <email>`.

Avoid `--amend` on pushed commits; create a new commit instead. When a
pre-commit hook fails, the commit didn't happen — fix and re-stage, then
commit fresh.

## Dependency changes

After changing a runtime dep in `pyproject.toml`:

```bash
uv lock                                                # refresh dev lockfile
uv run scripts/dev/sync_requirements.py --write        # regenerate requirements files
uv run scripts/dev/check_env_examples.py --write       # regenerate .env.example
```

The Docker image installs from `pyproject.toml` directly via
`pip install ".[server]"`; the dev `uv.lock` is for reproducible
development environments only.
