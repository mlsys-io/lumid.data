"""``/storage/v1/*`` — REST over MinIO (Supabase Storage shape)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..auth.security import PrincipalContext, require_scope
from ..deps import get_state
from ..services import s3 as s3_svc
from ..services.audit import now_ms
from ..state import AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storage/v1", tags=["storage"])


@router.get("/object/{bucket}/{path:path}")
async def get_object(
    bucket: str,
    path: str,
    state: AppState = Depends(get_state),
    principal: PrincipalContext = Depends(require_scope("storage:read")),
) -> StreamingResponse:
    started = now_ms()
    chunks, meta = s3_svc.stream_get(state.s3_client, bucket, path)
    headers = {}
    if meta.get("content_type"):
        headers["Content-Type"] = meta["content_type"]
    if meta.get("etag"):
        headers["ETag"] = str(meta["etag"])
    await state.audit.record(
        principal=principal,
        surface="storage",
        op="GET",
        path=f"/storage/v1/object/{bucket}/{path}",
        status_code=200,
        latency_ms=now_ms() - started,
    )
    return StreamingResponse(
        chunks,
        media_type=meta.get("content_type") or "application/octet-stream",
        headers=headers,
    )


@router.put("/object/{bucket}/{path:path}", status_code=status.HTTP_201_CREATED)
async def put_object(
    bucket: str,
    path: str,
    request: Request,
    state: AppState = Depends(get_state),
    principal: PrincipalContext = Depends(require_scope("storage:write")),
) -> dict[str, str]:
    started = now_ms()
    payload = await request.body()
    content_type = request.headers.get("content-type")
    uri = s3_svc.put_idempotent(state.s3_client, bucket, path, payload, content_type)
    await state.audit.record(
        principal=principal,
        surface="storage",
        op="PUT",
        path=f"/storage/v1/object/{bucket}/{path}",
        status_code=201,
        latency_ms=now_ms() - started,
        request_meta={"size_bytes": len(payload)},
    )
    return {"uri": uri, "sha256": s3_svc.compute_sha256(payload)}


@router.delete("/object/{bucket}/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_object(
    bucket: str,
    path: str,
    state: AppState = Depends(get_state),
    principal: PrincipalContext = Depends(require_scope("storage:write")),
) -> None:
    started = now_ms()
    s3_svc.delete(state.s3_client, bucket, path)
    await state.audit.record(
        principal=principal,
        surface="storage",
        op="DELETE",
        path=f"/storage/v1/object/{bucket}/{path}",
        status_code=204,
        latency_ms=now_ms() - started,
    )


@router.get("/list/{bucket}")
async def list_bucket(
    bucket: str,
    prefix: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    state: AppState = Depends(get_state),
    principal: PrincipalContext = Depends(require_scope("storage:read")),
) -> dict[str, list]:
    started = now_ms()
    items = s3_svc.list_objects(state.s3_client, bucket, prefix, limit)
    await state.audit.record(
        principal=principal,
        surface="storage",
        op="LIST",
        path=f"/storage/v1/list/{bucket}",
        status_code=200,
        latency_ms=now_ms() - started,
        request_meta={"prefix": prefix, "limit": limit},
    )
    return {"items": items}


@router.post("/upload/sign/{bucket}/{path:path}")
async def sign_upload(
    bucket: str,
    path: str,
    expires: int = Query(300, ge=10, le=3600),
    content_type: str | None = None,
    state: AppState = Depends(get_state),
    principal: PrincipalContext = Depends(require_scope("storage:write")),
) -> dict[str, str | int]:
    if not state.s3_client:
        raise HTTPException(status_code=500, detail="s3 client not configured")
    url = s3_svc.presign_put(state.s3_client, bucket, path, expires, content_type)
    await state.audit.record(
        principal=principal,
        surface="storage",
        op="SIGN_PUT",
        path=f"/storage/v1/upload/sign/{bucket}/{path}",
        status_code=200,
        latency_ms=0.0,
    )
    return {"url": url, "expires": expires}


@router.post("/download/sign/{bucket}/{path:path}")
async def sign_download(
    bucket: str,
    path: str,
    expires: int = Query(300, ge=10, le=3600),
    state: AppState = Depends(get_state),
    principal: PrincipalContext = Depends(require_scope("storage:read")),
) -> dict[str, str | int]:
    url = s3_svc.presign_get(state.s3_client, bucket, path, expires)
    await state.audit.record(
        principal=principal,
        surface="storage",
        op="SIGN_GET",
        path=f"/storage/v1/download/sign/{bucket}/{path}",
        status_code=200,
        latency_ms=0.0,
    )
    return {"url": url, "expires": expires}
