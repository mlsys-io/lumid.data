"""Pytest fixtures shared across the lumid.data test suite."""

import pytest


@pytest.fixture
def utc_iso() -> str:
    return "2026-05-02T00:00:00+00:00"
