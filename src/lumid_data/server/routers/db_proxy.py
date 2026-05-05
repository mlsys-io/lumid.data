"""``/db/v1/*`` reverse-proxy to a PostgREST sidecar.

We don't reinvent URL-to-SQL parsing — PostgREST has done it for ten
years. Each request gets a freshly-minted JWT for the admin role and
streams unchanged through PostgREST. PostgREST still enforces any RLS
the operator has configured on the underlying tables.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, Request, Response

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


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(
    path: str,
    request: Request,
    state: AppState = Depends(get_state),
) -> Response:
    """Forward to PostgREST with a freshly-minted JWT."""
    started = now_ms()
    upstream_jwt = mint(state.postgrest_jwt)
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
