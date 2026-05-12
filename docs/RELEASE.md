# Release

`lumid.data` publishes only the SDK:

| Distribution | Source |
|--------------|--------|
| `lumid-data-sdk` | `sdk/` |

The `lumid-data` service itself is not published to PyPI — it ships as a
Docker image built from this repo. The root `pyproject.toml` still tracks the
synchronized version so the Docker build installs the matching SDK, but the
service wheel is never uploaded.

## PyPI setup

Use PyPI Trusted Publishing instead of long-lived API tokens. Configure a
pending or active trusted publisher for `lumid-data-sdk` on both PyPI and
TestPyPI.

Use this publisher configuration:

| Setting | Value |
|---------|-------|
| Owner | `mlsys-io` |
| Repository | `lumid.data` |
| Workflow | `release.yml` |
| Environment | `pypi` for PyPI, `testpypi` for TestPyPI |

Create matching GitHub environments named `pypi` and `testpypi`. The `pypi`
environment should require manual approval. The approver should verify the
matching TestPyPI run before approving production publishing.

## Prepare a release

1. Pick the next synchronized package version, for example `0.1.1`. The SDK
   and the service share a version so the tag uniquely identifies both the
   published SDK and the Docker image cut from the same commit.
2. Update package versions and the internal SDK pin:

   ```bash
   uv run scripts/dev/bump_version.py 0.1.1
   ```

3. Re-lock:

   ```bash
   uv lock
   ```

4. Validate the release metadata:

   ```bash
   uv run scripts/ci/check_release_version.py --tag v0.1.1
   ```

5. Build and smoke-test the SDK:

   ```bash
   uv sync --all-packages --group dev --frozen
   uv build --package lumid-data-sdk --out-dir dist
   uv run scripts/ci/check_package_build.py --dist dist
   ```

6. Run the normal validation suite:

   ```bash
   uv run pre-commit run --all-files
   uv run pytest tests/ --ignore=tests/integration -v
   ```

7. Open and merge a release prep PR with the version bump and `uv.lock`.

## Publish to TestPyPI

After the release prep PR lands, create and push a signed or annotated tag:

```bash
git tag -a v0.1.1 -m "chore: release v0.1.1"
git push origin v0.1.1
```

Run the `Release` workflow manually:

```bash
gh workflow run release.yml -f tag=v0.1.1 -f publish_target=testpypi
```

Then verify the published artifact from TestPyPI in a fresh environment:

```bash
python -m venv .venv-testpypi
. .venv-testpypi/bin/activate
python -m pip install --upgrade pip
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ lumid-data-sdk
python -c "from lumid_data.sdk import AsyncClient, Client; assert Client and AsyncClient"
```

## Publish to PyPI

Create a GitHub Release from the same `vX.Y.Z` tag. Publishing the release
triggers `.github/workflows/release.yml`, which rebuilds from the tag,
validates versions, smoke-tests the wheel, and publishes the uploaded
artifact to PyPI after the `pypi` environment approval.

Do not move or force-update release tags. The release workflow assumes the tag
already passed PR or main-branch CI, and moving a tag can bypass that validation
history.

After publishing, verify PyPI installs in a fresh environment:

```bash
python -m venv .venv-pypi
. .venv-pypi/bin/activate
python -m pip install --upgrade pip
python -m pip install lumid-data-sdk
python -c "from lumid_data.sdk import AsyncClient, Client; assert Client and AsyncClient"
```

## If a release goes wrong

PyPI versions are immutable: once `vX.Y.Z` is published you cannot edit,
re-upload, or replace it. Recovery options:

- **Yank** the bad release on PyPI. `pip install` still installs the version
  when it is explicitly pinned, but resolution skips it otherwise. Use yank
  for security or correctness bugs that warrant skipping the version
  entirely.
- **Cut the next patch.** Bump to `vX.Y.(Z+1)`, fix forward, and publish.
  This is the default path for any non-critical bug.
- **`.postN` re-release** of the same source release when the only change is
  packaging metadata (LICENSE, classifiers, README) and no Python code
  changed. Rare.

Do not delete or reuse a published version number under any circumstance.
