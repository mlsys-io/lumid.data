"""Lumid (lum.id) federated identity plugin.

Mirrors the Lumilake plugin verbatim — per memory ``Lumilake/FlowMesh
auth parity`` the introspect path and caching behavior must match
across services. Differs only in that lumid.data has no governance
``principals`` table to upsert into; we map the introspected token
straight to a ``PrincipalContext``.
"""

import hashlib
import logging
import os
import time
from typing import Any

import httpx
from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..server.auth.security import ALLOWED_SCOPES, PrincipalContext

LUMID_IDENTITY_URL = os.getenv("LUMID_IDENTITY_URL", "https://lum.id").rstrip("/")
_INTROSPECT_CACHE_TTL_SEC = 60
_INTROSPECT_CACHE_CAPACITY = 10_000
_LUMID_ORG_ID = "lumid"


class IntrospectedToken(BaseModel):
    model_config = ConfigDict(extra="ignore")

    active: bool = False
    sub: str | None = None
    email: str | None = None
    scopes: list[str] = Field(default_factory=list)
    source: str | None = None
    reason: str | None = None
    stored_at: float

    def model_post_init(self, context: Any) -> None:
        if self.active and not self.sub:
            if isinstance(context, dict) and (logger := context.get("logger")):
                logger.warning("lum.id introspect active=true but missing sub")
            self.active = False
            self.reason = self.reason or "missing sub"


_cache: dict[str, IntrospectedToken] = {}


async def introspect_token(raw_key: str, logger: logging.Logger) -> IntrospectedToken | None:
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    now = time.time()
    cached = _cache.get(digest)
    if cached and (now - cached.stored_at) < _INTROSPECT_CACHE_TTL_SEC:
        return cached

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                f"{LUMID_IDENTITY_URL}/oauth/introspect",
                data={"token": raw_key},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        logger.warning("lum.id introspect network error: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning("lum.id introspect status=%d body=%s", resp.status_code, resp.text[:200])
        return None
    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        logger.warning("lum.id introspect non-JSON body: %s", resp.text[:200])
        return None
    if not isinstance(body, dict):
        logger.warning("lum.id introspect non-object body: %r", body)
        return None

    creds = IntrospectedToken.model_validate(
        {**body, "stored_at": now}, context={"logger": logger}
    )
    if creds.active:
        _prune(now)
        _cache[digest] = creds
    return creds


def _prune(now: float) -> None:
    cutoff = now - _INTROSPECT_CACHE_TTL_SEC
    while _cache:
        key = next(iter(_cache))
        if _cache[key].stored_at >= cutoff and len(_cache) < _INTROSPECT_CACHE_CAPACITY:
            break
        del _cache[key]


def map_scopes_for_lumid_data(scopes: list[str]) -> list[str]:
    """Translate lum.id namespaced scopes to lumid.data's local vocabulary."""
    out: list[str] = []
    prefix_data = "lumid_data:"
    prefix_lumilake = "lumilake:"
    for s in scopes:
        if s == "*":
            out.append("*")
        elif s == f"{prefix_data}*" or s == f"{prefix_lumilake}*":
            out.append("*")
        elif s.startswith(prefix_data):
            out.append(s.removeprefix(prefix_data))
        elif s.startswith(prefix_lumilake):
            out.append(s.removeprefix(prefix_lumilake))
    return out


class LumidIdentityProvider:
    name = "lumid"

    async def resolve(self, raw_token: str, logger: logging.Logger) -> PrincipalContext | None:
        introspected = await introspect_token(raw_token, logger)
        if introspected is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Identity provider unavailable",
            )
        if not introspected.active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid API key ({introspected.reason or 'inactive'})",
            )
        assert introspected.sub is not None
        scopes = [s for s in map_scopes_for_lumid_data(introspected.scopes) if s in ALLOWED_SCOPES]
        return PrincipalContext(
            principal_id=introspected.sub,
            org_id=_LUMID_ORG_ID,
            external_id=introspected.sub,
            principal_type="user",
            scopes=scopes,
        )
