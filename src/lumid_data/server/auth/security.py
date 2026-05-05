"""Minimal auth surface for OSS.

OSS ships no native API-key auth. The semantic is:

- With no `IdentityProvider` plugins registered, `authenticate_api_key`
  returns a default admin principal — auth is effectively a no-op and every
  caller is admin. Routers call `default_principal()` directly to short-circuit
  auth in the unconfigured case.
- Once at least one provider is registered, every bearer token is routed
  through the chain in registration order. The first provider returning a
  non-`None` `PrincipalContext` wins; if none claim the token, 401 is raised.

This module exists for two reasons:

1. The hook protocol (`server.hooks.identity`) references `PrincipalContext`
   as its canonical principal type. Keeping the dataclass here matches the
   contract third-party plugins compile against.

2. `authenticate_api_key` is a thin wrapper around the identity-provider
   chain. OSS routers do not depend on this function today; it is kept for
   any external user of the hook contract that wants to exercise the chain.
"""

import logging
from dataclasses import dataclass

from fastapi import HTTPException, status


@dataclass(frozen=True)
class PrincipalContext:
    principal_id: str
    org_id: str
    external_id: str
    principal_type: str
    scopes: list[str]


def default_principal() -> PrincipalContext:
    """Synthetic admin principal returned when no auth is configured."""
    return PrincipalContext(
        principal_id="admin",
        org_id="local",
        external_id="local",
        principal_type="admin",
        scopes=["*"],
    )


async def authenticate_api_key(
    raw_key: str, logger: logging.Logger
) -> PrincipalContext:
    """Resolve a bearer token to a `PrincipalContext` via registered providers.

    With no providers registered, returns `default_principal()` — auth is off,
    every caller is admin.
    """
    from ..hooks import IDENTITY_PROVIDERS

    if not IDENTITY_PROVIDERS:
        return default_principal()

    for provider in IDENTITY_PROVIDERS:
        resolved = await provider.resolve(raw_key, logger)
        if resolved is not None:
            return resolved

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No identity provider accepted the token",
    )
