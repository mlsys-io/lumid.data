"""Bearer-token auth.

OSS lumid.data ships **no DB-backed principals**. The plugin chain in
``IDENTITY_PROVIDERS`` resolves a bearer token to a ``PrincipalContext``
when configured (e.g. the OIDC introspector). With no provider
registered, auth is a no-op and any request is treated as the default
admin principal. This matches local-dev shape.
"""

import logging
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ALLOWED_SCOPES: set[str] = {
    "*",
    "db:read",
    "db:write",
    "storage:read",
    "storage:write",
    "sql:read",
    "sql:write",
    "agent:run",
    "admin:audit",
    "streams:read",
    "streams:write",
    "ingest:write",
}

_DEFAULT_ADMIN = "default-admin"

_bearer = HTTPBearer(auto_error=False)


@dataclass
class PrincipalContext:
    principal_id: str
    org_id: str | None = None
    external_id: str | None = None
    principal_type: str = "user"
    scopes: list[str] = field(default_factory=list)


async def authenticate_bearer(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> PrincipalContext:
    """Resolve a bearer token to a principal via the registered chain.

    No providers registered → returns a default admin principal (OSS).
    Otherwise the first provider that returns non-None wins; if all
    return None or raise 401, the request is rejected.
    """
    from ..hooks import IDENTITY_PROVIDERS

    if not IDENTITY_PROVIDERS:
        return PrincipalContext(principal_id=_DEFAULT_ADMIN, scopes=["*"])

    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )

    logger = logging.getLogger("lumid_data.auth")
    last_exc: HTTPException | None = None
    for provider in IDENTITY_PROVIDERS:
        try:
            principal = await provider.resolve(creds.credentials, logger)
        except HTTPException as exc:
            last_exc = exc
            continue
        if principal is not None:
            return principal
    if last_exc is not None:
        raise last_exc
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
    )


def require_scope(scope: str):
    async def _dep(
        principal: PrincipalContext = Depends(authenticate_bearer),
    ) -> PrincipalContext:
        if "*" in principal.scopes or scope in principal.scopes:
            return principal
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"Scope {scope!r} required"
        )

    return _dep
