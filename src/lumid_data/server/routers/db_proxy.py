"""``/db/v1/*`` reverse-proxy to a PostgREST sidecar.

We don't reinvent URL-to-SQL parsing — PostgREST has done it for ten
years. Our job here is auth + JWT minting + passthrough:

1. The user's bearer token is validated by lumid.data
   (``authenticate_bearer`` resolves it to a ``PrincipalContext``).
2. We mint a short-lived PostgREST JWT keyed on the principal's
   resolved Postgres role and forward the request upstream.
3. The response body streams back unchanged.

PostgREST enforces row-level security policies the operator has set on
each table. We do **not** translate query strings or HTTP verbs — every
query semantics is whatever PostgREST gives us.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..auth.security import PrincipalContext, authenticate_bearer
from ..deps import get_state
from ..services.audit import now_ms
from ..services.postgrest_jwt import mint
from ..state import AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/db/v1", tags=["db"])

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",  # codespell:ignore te
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "authorization",
}

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def _scope_for(method: str) -> str:
    return "db:read" if method in {"GET", "HEAD", "OPTIONS"} else "db:write"


def _authorize(method: str, principal: PrincipalContext) -> None:
    needed = _scope_for(method)
    if "*" in principal.scopes or needed in principal.scopes:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail=f"Scope {needed!r} required"
    )


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(
    path: str,
    request: Request,
    state: AppState = Depends(get_state),
    principal: PrincipalContext = Depends(authenticate_bearer),
) -> Response:
    """Forward to PostgREST with a freshly-minted JWT."""
    _authorize(request.method, principal)
    started = now_ms()
    upstream_jwt = mint(state.postgrest_jwt, principal)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    headers["Authorization"] = f"Bearer {upstream_jwt}"
    body = await request.body()
    url = f"{state.settings.postgrest_url.rstrip('/')}/{path}"
    params = dict(request.query_params)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        upstream = await client.request(
            request.method, url, params=params, headers=headers, content=body
        )

    out_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    elapsed = now_ms() - started
    await state.audit.record(
        principal=principal,
        surface="db",
        op=request.method,
        path=f"/db/v1/{path}",
        status_code=upstream.status_code,
        latency_ms=elapsed,
        request_meta={"params": params},
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=out_headers,
    )
