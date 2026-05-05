"""Auth surface: empty-chain default + provider chain semantics."""

import logging

import pytest
from fastapi import HTTPException

from lumid_data.server.auth.security import (
    PrincipalContext,
    authenticate_api_key,
    default_principal,
)
from lumid_data.server.hooks import IDENTITY_PROVIDERS


@pytest.fixture(autouse=True)
def _clear_providers():
    IDENTITY_PROVIDERS.clear()
    yield
    IDENTITY_PROVIDERS.clear()


def test_default_principal_is_admin() -> None:
    p = default_principal()
    assert p.principal_id == "admin"
    assert p.scopes == ["*"]


@pytest.mark.asyncio
async def test_no_providers_returns_default_admin() -> None:
    p = await authenticate_api_key("any-token", logging.getLogger())
    assert p == default_principal()


@pytest.mark.asyncio
async def test_first_claiming_provider_wins() -> None:
    claimed = PrincipalContext(
        principal_id="alice",
        org_id="acme",
        external_id="ext-1",
        principal_type="user",
        scopes=["sql:read"],
    )

    class _Skip:
        name = "skip"

        async def resolve(self, raw_token, logger):
            return None

    class _Claim:
        name = "claim"

        async def resolve(self, raw_token, logger):
            return claimed

    IDENTITY_PROVIDERS.extend([_Skip(), _Claim()])
    p = await authenticate_api_key("tok", logging.getLogger())
    assert p == claimed


@pytest.mark.asyncio
async def test_no_provider_claims_raises_401() -> None:
    class _Skip:
        name = "skip"

        async def resolve(self, raw_token, logger):
            return None

    IDENTITY_PROVIDERS.append(_Skip())
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key("tok", logging.getLogger())
    assert exc.value.status_code == 401
