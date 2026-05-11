# Release

`lumid.data` publishes the service package and SDK package:

| Distribution | Source |
|--------------|--------|
| `lumid-data` | `pyproject.toml` |
| `lumid-data-sdk` | `sdk/` |

## PyPI setup

Use PyPI Trusted Publishing instead of long-lived API tokens. Configure pending
or active trusted publishers for both distributions on PyPI and TestPyPI.

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

1. Pick the next synchronized package version, for example `0.1.1`.
2. Update package versions and first-party pins:

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

5. Build and smoke-test the distributions:

   ```bash
   uv sync --all-packages --group dev --frozen
   uv build --all-packages --out-dir dist
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

Then verify the published artifacts from TestPyPI in a fresh environment:

```bash
python -m venv .venv-testpypi
. .venv-testpypi/bin/activate
python -m pip install --upgrade pip
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ lumid-data-sdk
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ lumid-data
python -c "from lumid_data.sdk import Client; import lumid_data.server.main; assert Client"
lumid-data --help
```

## Publish to PyPI

Create a GitHub Release from the same `vX.Y.Z` tag. Publishing the release
triggers `.github/workflows/release.yml`, which rebuilds from the tag, runs
tests, validates versions, smoke-tests the wheels, and publishes the uploaded
artifact set to PyPI after the `pypi` environment approval.

Do not move or force-update release tags. The release workflow assumes the tag
already passed PR or main-branch CI, and moving a tag can bypass that validation
history.

After publishing, verify PyPI installs in a fresh environment:

```bash
python -m venv .venv-pypi
. .venv-pypi/bin/activate
python -m pip install --upgrade pip
python -m pip install lumid-data-sdk
python -m pip install lumid-data
python -c "from lumid_data.sdk import Client; import lumid_data.server.main; assert Client"
lumid-data --help
```
