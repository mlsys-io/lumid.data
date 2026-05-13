"""lumid.data SDK — thin HTTP wrapper, one method per surface."""

import json
import logging
import os
import tomllib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .schemas import (
    RetrievalRequest,
    RetrievalResult,
    SignedUrl,
    SqlResult,
    StorageList,
    StorageObject,
    StoragePutResult,
)

logger = logging.getLogger(__name__)


class ClientError(RuntimeError):
    """Raised when a data-plane call fails after the network layer."""


@dataclass(frozen=True)
class Credentials:
    """Typed view over ``~/.lumid/apps/<app>/credentials.toml``."""

    app: str
    raw: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def required(self, key: str) -> str:
        value = self.raw.get(key)
        if value is None:
            raise KeyError(f"missing required credential {key!r} in app {self.app!r}")
        return str(value)


class Client:
    """Sync client over lumid.data's unified URL."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_sec: float = 30.0,
    ) -> None:
        self._base_url = (base_url or os.environ["LUMID_DATA_URL"]).rstrip("/")
        self._token = token or os.environ.get("LUMID_TOKEN")
        self._timeout = httpx.Timeout(timeout_sec, connect=5.0)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if extra:
            h.update(extra)
        return h

    # ── /storage ──────────────────────────────────────────────────

    def storage_get(self, bucket: str, path: str) -> bytes:
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(
                f"{self._base_url}/storage/v1/object/{bucket}/{path}",
                headers=self._headers(),
            )
        if r.status_code >= 300:
            raise ClientError(f"storage_get failed ({r.status_code}): {r.text}")
        return r.content

    def storage_stat(self, bucket: str, path: str) -> bool:
        """``True`` when ``path`` exists under ``bucket``.

        lumid.data's REST surface has no HEAD; probe via list with the full
        key as prefix and check for an exact match.
        """
        items = self.storage_list(bucket, prefix=path, limit=1)
        return any(it.key == path for it in items)

    def storage_put(
        self, bucket: str, path: str, content: bytes, mime: str | None = None
    ) -> StoragePutResult:
        headers = self._headers({"Content-Type": mime or "application/octet-stream"})
        with httpx.Client(timeout=self._timeout) as c:
            r = c.put(
                f"{self._base_url}/storage/v1/object/{bucket}/{path}",
                headers=headers,
                content=content,
            )
        return StoragePutResult.model_validate(_ok_json(r, "storage_put"))

    def storage_delete(self, bucket: str, path: str) -> None:
        with httpx.Client(timeout=self._timeout) as c:
            r = c.delete(
                f"{self._base_url}/storage/v1/object/{bucket}/{path}",
                headers=self._headers(),
            )
        if r.status_code >= 300:
            raise ClientError(f"storage_delete failed ({r.status_code}): {r.text}")

    def storage_list(
        self, bucket: str, prefix: str | None = None, limit: int = 100
    ) -> list[StorageObject]:
        params: dict[str, str] = {"limit": str(limit)}
        if prefix:
            params["prefix"] = prefix
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(
                f"{self._base_url}/storage/v1/list/{bucket}",
                headers=self._headers(),
                params=params,
            )
        return StorageList.model_validate(_ok_json(r, "storage_list")).items

    def storage_sign_put(
        self,
        bucket: str,
        path: str,
        expires: int = 300,
        content_type: str | None = None,
    ) -> SignedUrl:
        params: dict[str, str] = {"expires": str(expires)}
        if content_type:
            params["content_type"] = content_type
        with httpx.Client(timeout=self._timeout) as c:
            r = c.post(
                f"{self._base_url}/storage/v1/upload/sign/{bucket}/{path}",
                headers=self._headers(),
                params=params,
            )
        return SignedUrl.model_validate(_ok_json(r, "storage_sign_put"))

    # ── /sql ──────────────────────────────────────────────────────

    def sql(self, query: str, params: list | None = None) -> SqlResult:
        with httpx.Client(timeout=self._timeout) as c:
            r = c.post(
                f"{self._base_url}/sql/v1",
                headers=self._headers(),
                json={"query": query, "params": params or []},
            )
        return SqlResult.model_validate(_ok_json(r, "sql"))

    # ── agent retrieval ───────────────────────────────────────────

    def retrieve(
        self,
        description: str,
        *,
        schema_scope: str | None = None,
        output_format: str | None = None,
        max_steps: int | None = None,
        model: str | None = None,
    ) -> RetrievalResult:
        """Plan + execute an NL-driven data retrieval through ``/agent/v1``.

        Returns a :class:`RetrievalResult` carrying a presigned download URL
        for the materialized file plus the lineage record (access chain,
        run_id, transcript URL, token + step counts).
        """
        request = RetrievalRequest(
            description=description,
            schema_scope=schema_scope,
            output_format=output_format,  # type: ignore[arg-type]
            max_steps=max_steps,
            model=model,
        )
        result: RetrievalResult | None = None
        last_error: Any = None
        for event, payload in self.agent_run(
            _retrieve_goal(request),
            skills=["data_retrieval"],
            tools_allowed=_RETRIEVAL_TOOL_ALLOWLIST,
            max_steps=max_steps,
            model=model,
        ):
            if event == "tool_result":
                if payload.get("name") != "replay_retrieval_plan":
                    continue
                tool_result = payload.get("result", {})
                if tool_result.get("status_code", 500) < 300:
                    body = tool_result.get("body")
                    if isinstance(body, dict) and "materialized_uri" in body:
                        result = RetrievalResult.model_validate(body)
                else:
                    last_error = tool_result.get("body")
            elif event == "error":
                last_error = payload
            elif event == "done":
                if result is not None:
                    result.tokens_in = int(payload.get("tokens_in", result.tokens_in))
                    result.tokens_out = int(
                        payload.get("tokens_out", result.tokens_out)
                    )
                    result.steps_taken = int(payload.get("steps", result.steps_taken))
                else:
                    last_error = {
                        "status": payload.get("status"),
                        "error": payload.get("error"),
                        "steps": payload.get("steps"),
                        "final_text": payload.get("final_text"),
                    }
        if result is None:
            raise ClientError(
                f"retrieve failed: {last_error or 'no materialized result'}"
            )
        return result

    def retrieve_to_file(
        self,
        description: str,
        *,
        schema_scope: str | None = None,
        out_path: str | Path,
        output_format: str | None = None,
        max_steps: int | None = None,
        model: str | None = None,
        verify: bool = True,
    ) -> RetrievalResult:
        """Convenience wrapper: ``retrieve`` + download in one call.

        Materializes the result file at ``out_path``; returns the
        :class:`RetrievalResult` (lineage + access chain) for downstream
        bookkeeping. Avoids forcing every consumer to fetch the presigned
        URL themselves — ergonomic for connector-style code paths.

        ``verify`` controls TLS verification on the download. Self-signed
        certs on internal MinIO endpoints commonly need ``verify=False``;
        prefer pinning a CA bundle path when running in production.
        """
        result = self.retrieve(
            description=description,
            schema_scope=schema_scope,
            output_format=output_format,
            max_steps=max_steps,
            model=model,
        )
        with httpx.Client(timeout=httpx.Timeout(None, connect=5.0), verify=verify) as c:
            r = c.get(result.signed_url)
            r.raise_for_status()
            Path(out_path).write_bytes(r.content)
        return result

    # ── /healthz ──────────────────────────────────────────────────

    def healthz(self) -> bool:
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(f"{self._base_url}/healthz", headers=self._headers())
        return r.status_code < 300

    # ── /agent ────────────────────────────────────────────────────

    def agent_run(
        self,
        goal: str,
        *,
        context: dict[str, Any] | None = None,
        skills: list[str] | None = None,
        tools_allowed: list[str] | None = None,
        max_steps: int | None = None,
        model: str | None = None,
    ) -> Iterator[tuple[str, Any]]:
        """Stream SSE events from the agent.

        Yields ``(event_name, payload)`` tuples. The final event is
        ``("done", {...})`` with the run summary.
        """
        body: dict[str, Any] = {"goal": goal}
        if context is not None:
            body["context"] = context
        if skills is not None:
            body["skills"] = skills
        if tools_allowed is not None:
            body["tools_allowed"] = tools_allowed
        if max_steps is not None:
            body["max_steps"] = max_steps
        if model is not None:
            body["model"] = model
        with httpx.Client(timeout=httpx.Timeout(None, connect=5.0)) as c:
            with c.stream(
                "POST",
                f"{self._base_url}/agent/v1",
                headers=self._headers({"Accept": "text/event-stream"}),
                json=body,
            ) as resp:
                if resp.status_code >= 300:
                    raise ClientError(
                        f"agent run failed ({resp.status_code}): {resp.read().decode()}"
                    )
                yield from _iter_sse(resp.iter_lines())

    async def agent_run_async(
        self, goal: str, **kwargs: Any
    ) -> AsyncIterator[tuple[str, Any]]:
        """Async variant of :meth:`agent_run`."""
        body: dict[str, Any] = {"goal": goal, **kwargs}
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0)) as c:
            async with c.stream(
                "POST",
                f"{self._base_url}/agent/v1",
                headers=self._headers({"Accept": "text/event-stream"}),
                json=body,
            ) as resp:
                if resp.status_code >= 300:
                    body_bytes = await resp.aread()
                    raise ClientError(
                        f"agent run failed ({resp.status_code}): {body_bytes.decode()}"
                    )
                async for ev in _aiter_sse(resp.aiter_lines()):
                    yield ev

    # ── creds ─────────────────────────────────────────────────────

    def creds(self, app: str) -> Credentials:
        path = Path.home() / ".lumid" / "apps" / app / "credentials.toml"
        if not path.exists():
            return Credentials(app=app, raw={})
        with path.open("rb") as f:
            data = tomllib.load(f)
        return Credentials(app=app, raw=data)


class AsyncClient:
    """Async client over lumid.data's unified URL — same surface as :class:`Client`."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_sec: float = 30.0,
    ) -> None:
        self._base_url = (base_url or os.environ["LUMID_DATA_URL"]).rstrip("/")
        self._token = token or os.environ.get("LUMID_TOKEN")
        self._timeout = httpx.Timeout(timeout_sec, connect=5.0)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if extra:
            h.update(extra)
        return h

    # ── /storage ──────────────────────────────────────────────────

    async def storage_get(self, bucket: str, path: str) -> bytes:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{self._base_url}/storage/v1/object/{bucket}/{path}",
                headers=self._headers(),
            )
        if r.status_code >= 300:
            raise ClientError(f"storage_get failed ({r.status_code}): {r.text}")
        return r.content

    async def storage_stat(self, bucket: str, path: str) -> bool:
        items = await self.storage_list(bucket, prefix=path, limit=1)
        return any(it.key == path for it in items)

    async def storage_put(
        self, bucket: str, path: str, content: bytes, mime: str | None = None
    ) -> StoragePutResult:
        headers = self._headers({"Content-Type": mime or "application/octet-stream"})
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.put(
                f"{self._base_url}/storage/v1/object/{bucket}/{path}",
                headers=headers,
                content=content,
            )
        return StoragePutResult.model_validate(_ok_json(r, "storage_put"))

    async def storage_delete(self, bucket: str, path: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.delete(
                f"{self._base_url}/storage/v1/object/{bucket}/{path}",
                headers=self._headers(),
            )
        if r.status_code >= 300:
            raise ClientError(f"storage_delete failed ({r.status_code}): {r.text}")

    async def storage_list(
        self, bucket: str, prefix: str | None = None, limit: int = 100
    ) -> list[StorageObject]:
        params: dict[str, str] = {"limit": str(limit)}
        if prefix:
            params["prefix"] = prefix
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{self._base_url}/storage/v1/list/{bucket}",
                headers=self._headers(),
                params=params,
            )
        return StorageList.model_validate(_ok_json(r, "storage_list")).items

    async def storage_sign_put(
        self,
        bucket: str,
        path: str,
        expires: int = 300,
        content_type: str | None = None,
    ) -> SignedUrl:
        params: dict[str, str] = {"expires": str(expires)}
        if content_type:
            params["content_type"] = content_type
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(
                f"{self._base_url}/storage/v1/upload/sign/{bucket}/{path}",
                headers=self._headers(),
                params=params,
            )
        return SignedUrl.model_validate(_ok_json(r, "storage_sign_put"))

    # ── /sql ──────────────────────────────────────────────────────

    async def sql(self, query: str, params: list | None = None) -> SqlResult:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(
                f"{self._base_url}/sql/v1",
                headers=self._headers(),
                json={"query": query, "params": params or []},
            )
        return SqlResult.model_validate(_ok_json(r, "sql"))

    # ── agent retrieval ───────────────────────────────────────────

    async def retrieve(
        self,
        description: str,
        *,
        schema_scope: str | None = None,
        output_format: str | None = None,
        max_steps: int | None = None,
        model: str | None = None,
    ) -> RetrievalResult:
        request = RetrievalRequest(
            description=description,
            schema_scope=schema_scope,
            output_format=output_format,  # type: ignore[arg-type]
            max_steps=max_steps,
            model=model,
        )
        result: RetrievalResult | None = None
        last_error: Any = None
        async for event, payload in self.agent_run(
            _retrieve_goal(request),
            skills=["data_retrieval"],
            tools_allowed=_RETRIEVAL_TOOL_ALLOWLIST,
            max_steps=max_steps,
            model=model,
        ):
            if event == "tool_result":
                if payload.get("name") != "replay_retrieval_plan":
                    continue
                tool_result = payload.get("result", {})
                if tool_result.get("status_code", 500) < 300:
                    body = tool_result.get("body")
                    if isinstance(body, dict) and "materialized_uri" in body:
                        result = RetrievalResult.model_validate(body)
                else:
                    last_error = tool_result.get("body")
            elif event == "error":
                last_error = payload
            elif event == "done":
                if result is not None:
                    result.tokens_in = int(payload.get("tokens_in", result.tokens_in))
                    result.tokens_out = int(
                        payload.get("tokens_out", result.tokens_out)
                    )
                    result.steps_taken = int(payload.get("steps", result.steps_taken))
                else:
                    last_error = {
                        "status": payload.get("status"),
                        "error": payload.get("error"),
                        "steps": payload.get("steps"),
                        "final_text": payload.get("final_text"),
                    }
        if result is None:
            raise ClientError(
                f"retrieve failed: {last_error or 'no materialized result'}"
            )
        return result

    async def retrieve_to_file(
        self,
        description: str,
        *,
        schema_scope: str | None = None,
        out_path: str | Path,
        output_format: str | None = None,
        max_steps: int | None = None,
        model: str | None = None,
        verify: bool = True,
    ) -> RetrievalResult:
        """Async retrieve + download in one call.

        See :meth:`Client.retrieve_to_file` for the sync equivalent.
        """
        result = await self.retrieve(
            description=description,
            schema_scope=schema_scope,
            output_format=output_format,
            max_steps=max_steps,
            model=model,
        )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=5.0), verify=verify
        ) as c:
            r = await c.get(result.signed_url)
            r.raise_for_status()
            Path(out_path).write_bytes(r.content)
        return result

    # ── /healthz ──────────────────────────────────────────────────

    async def healthz(self) -> bool:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(f"{self._base_url}/healthz", headers=self._headers())
        return r.status_code < 300

    # ── /agent ────────────────────────────────────────────────────

    async def agent_run(
        self, goal: str, **kwargs: Any
    ) -> AsyncIterator[tuple[str, Any]]:
        body: dict[str, Any] = {"goal": goal, **kwargs}
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0)) as c:
            async with c.stream(
                "POST",
                f"{self._base_url}/agent/v1",
                headers=self._headers({"Accept": "text/event-stream"}),
                json=body,
            ) as resp:
                if resp.status_code >= 300:
                    body_bytes = await resp.aread()
                    raise ClientError(
                        f"agent run failed ({resp.status_code}): {body_bytes.decode()}"
                    )
                async for ev in _aiter_sse(resp.aiter_lines()):
                    yield ev


def _ok_json(resp: httpx.Response, op: str) -> Any:
    if resp.status_code >= 300:
        raise ClientError(f"{op} failed ({resp.status_code}): {resp.text}")
    return resp.json() if resp.content else None


_RETRIEVAL_TOOL_ALLOWLIST = [
    "get_schema_cards",
    "post_sql_v1_explain",
    "post_sql_v1_count",
    "post_sql_v1_sample",
    "get_storage_v1_list_bucket",
    "get_storage_v1_stat_bucket_path",
    "replay_retrieval_plan",
]


def _retrieve_goal(request: RetrievalRequest) -> str:
    parts = [
        "Handle this as a data retrieval workflow.",
        f"User request: {request.description}",
        "Use get_schema_cards first.",
        "Use explain/count/sample preview-stat probes when needed.",
        "Use SQL identifiers from schema cards exactly, including double quotes.",
        "Compose the final SQL/storage retrieval plan yourself.",
        "Call replay_retrieval_plan to materialize the result.",
        "If replay returns an SQL error, fix the plan and call replay again.",
        "Do not call tools that return full rows or object bytes to the model.",
        "After replay, summarize only preview/stat metadata such as row count "
        "and size; do not try to view the full materialized result.",
    ]
    if request.schema_scope:
        parts.append(f"Schema scope: {request.schema_scope}")
    if request.output_format:
        parts.append(f"Output format: {request.output_format}")
    return "\n".join(parts)


def _iter_sse(lines: Iterator[str]) -> Iterator[tuple[str, Any]]:
    event = "message"
    data_lines: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                yield event, _try_json(payload)
            event = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    if data_lines:
        yield event, _try_json("\n".join(data_lines))


async def _aiter_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, Any]]:
    event = "message"
    data_lines: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                yield event, _try_json(payload)
            event = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    if data_lines:
        yield event, _try_json("\n".join(data_lines))


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text
