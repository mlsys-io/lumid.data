"""Mint short-lived JWTs the way PostgREST expects.

PostgREST accepts a Bearer JWT signed with a shared secret. The ``role``
claim selects the Postgres role the request runs as; lumid.data has no
auth, so every request runs as the admin role.
"""

import time
from dataclasses import dataclass

import jwt


@dataclass(frozen=True)
class PostgrestJwtConfig:
    secret: str
    ttl_sec: int
    anon_role: str
    user_role: str
    admin_role: str
    algorithm: str = "HS256"


def mint(cfg: PostgrestJwtConfig) -> str:
    now = int(time.time())
    payload = {
        "role": cfg.admin_role,
        "iat": now,
        "exp": now + cfg.ttl_sec,
    }
    return jwt.encode(payload, cfg.secret, algorithm=cfg.algorithm)
