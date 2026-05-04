"""Bearer auth + scope enforcement."""

import logging

import pytest
from fastapi import HTTPException

from lumid_data.server.auth.security import (
    PrincipalContext,
    authenticate_bearer,
    require_scope,
)
from lumid_data.server.hooks import IDENTITY_PROVIDERS


@pytest.fixture(autouse=True)
def _cleanup_providers():
    yield
    IDENTITY_PROVIDERS.clear()


async def test_no_provider_returns_default_admin() -> None:
    principal = await authenticate_bearer(creds=None)
    assert principal.principal_id == "default-admin"
    assert "*" in principal.scopes


async def test_missing_token_when_provider_registered_raises_401() -> None:
    class _Stub:
        name = "stub"

        async def resolve(self, raw, log):
            return None

    IDENTITY_PROVIDERS.append(_Stub())
    with pytest.raises(HTTPException) as exc:
        await authenticate_bearer(creds=None)
    assert exc.value.status_code == 401


async def test_provider_resolves_principal() -> None:
    class _Stub:
        name = "stub"

        async def resolve(self, raw, log: logging.Logger) -> PrincipalContext | None:
            return PrincipalContext(principal_id="alice", scopes=["db:read"])

    IDENTITY_PROVIDERS.append(_Stub())
    from fastapi.security import HTTPAuthorizationCredentials

    principal = await authenticate_bearer(
        creds=HTTPAuthorizationCredentials(scheme="Bearer", credentials="t")
    )
    assert principal.principal_id == "alice"


async def test_require_scope_enforces() -> None:
    dep = require_scope("db:write")
    with pytest.raises(HTTPException) as exc:
        await dep(principal=PrincipalContext(principal_id="x", scopes=["db:read"]))
    assert exc.value.status_code == 403


async def test_require_scope_passes_with_wildcard() -> None:
    dep = require_scope("db:write")
    principal = await dep(principal=PrincipalContext(principal_id="x", scopes=["*"]))
    assert principal.principal_id == "x"
