"""Mint short-lived JWTs the way PostgREST expects.

PostgREST accepts a Bearer JWT signed with a shared secret. The ``role``
claim selects the Postgres role the request runs as; we map a
``PrincipalContext`` to one of three operator-defined roles based on
the principal's scopes.
"""

import time
from dataclasses import dataclass

import jwt

from ..auth.security import PrincipalContext


@dataclass(frozen=True)
class PostgrestJwtConfig:
    secret: str
    ttl_sec: int
    anon_role: str
    user_role: str
    admin_role: str
    algorithm: str = "HS256"


def _resolve_role(principal: PrincipalContext, cfg: PostgrestJwtConfig) -> str:
    scopes = set(principal.scopes)
    if "*" in scopes or "admin:audit" in scopes:
        return cfg.admin_role
    if "db:write" in scopes or "db:read" in scopes:
        return cfg.user_role
    return cfg.anon_role


def mint(cfg: PostgrestJwtConfig, principal: PrincipalContext) -> str:
    now = int(time.time())
    role = _resolve_role(principal, cfg)
    payload = {
        "role": role,
        "principal_id": principal.principal_id,
        "iat": now,
        "exp": now + cfg.ttl_sec,
    }
    if principal.org_id:
        payload["org_id"] = principal.org_id
    return jwt.encode(payload, cfg.secret, algorithm=cfg.algorithm)
