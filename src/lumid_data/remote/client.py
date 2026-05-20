"""MCP client over SSE for a remote read-only data source.

Stateless: ``aopen()`` opens one session for tool discovery, lists +
caches the tools, and closes. Each ``call()`` opens a fresh session.
That avoids the anyio cross-task cancel-scope hazard that a long-lived
session hits under pytest-asyncio and per-request FastAPI handlers.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ..agent.providers.base import ToolDef

logger = logging.getLogger("lumid_data.remote")


class RemoteMCPClient:
    """MCP-over-SSE client used by the agent and ``/mcp`` server.

    Tools are surfaced with a name prefix (default ``remote_``) to keep
    them distinct from lumid.data's own route-derived tools. If
    ``aopen()`` fails, the instance stays unopened — the lifespan treats
    this as "remote feature unavailable" and skips the wiring.
    """

    def __init__(self, url: str, token: str | None, prefix: str = "remote_") -> None:
        self._url = url
        self._headers: dict[str, str] = (
            {"Authorization": f"Bearer {token}"} if token else {}
        )
        self._prefix = prefix
        self._tools: list[ToolDef] = []
        self._opened = False

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def opened(self) -> bool:
        return self._opened

    def tools(self) -> list[ToolDef]:
        return list(self._tools)

    def owns(self, tool_name: str) -> bool:
        return tool_name.startswith(self._prefix)

    async def aopen(self) -> None:
        async with self._session() as session:
            listed = await session.list_tools()
            self._tools = [
                ToolDef(
                    name=f"{self._prefix}{t.name}",
                    description=(t.description or f"Remote tool {t.name}").strip(),
                    input_schema=t.inputSchema or {"type": "object"},
                )
                for t in listed.tools
            ]
        self._opened = True
        logger.info("remote MCP discovered: %s (%d tools)", self._url, len(self._tools))

    async def aclose(self) -> None:
        self._opened = False

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one prefixed tool call. Returns ``{status_code, body}``."""
        if not self.owns(name):
            return {
                "status_code": 400,
                "body": {"error": f"not a remote tool: {name!r}"},
            }
        if not self._opened:
            return {
                "status_code": 503,
                "body": {"error": "remote MCP client is not opened"},
            }
        unprefixed = name[len(self._prefix) :]
        try:
            async with self._session() as session:
                result = await session.call_tool(unprefixed, arguments)
        except Exception as exc:
            logger.exception("remote MCP call failed: %s", name)
            return {"status_code": 502, "body": {"error": repr(exc)}}
        return _coerce_result(result)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        """One MCP-over-SSE session for the duration of the ``async with``.

        Tests monkey-patch this to bypass the network.
        """
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(self._url, headers=self._headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def _coerce_result(result: Any) -> dict[str, Any]:
    """``CallToolResult`` → ``{status_code, body}``.

    Collapses text content blocks to a single body (parsed JSON if it
    looks like JSON, else raw text); non-text blocks become ``extra_blocks``.
    """
    is_error = bool(getattr(result, "isError", False))
    status_code = 502 if is_error else 200
    content = list(getattr(result, "content", []) or [])
    if not content:
        return {"status_code": status_code, "body": None}
    text_parts: list[str] = []
    structured: list[Any] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            text_parts.append(text)
        else:
            structured.append(_block_to_dict(block))
    if text_parts:
        joined = "\n".join(text_parts)
        import json

        try:
            body: Any = json.loads(joined)
        except (ValueError, TypeError):
            body = joined
        if structured:
            return {
                "status_code": status_code,
                "body": body,
                "extra_blocks": structured,
            }
        return {"status_code": status_code, "body": body}
    return {"status_code": status_code, "body": structured}


def _block_to_dict(block: Any) -> dict[str, Any]:
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump(mode="json")  # type: ignore[no-any-return]
    return {"type": getattr(block, "type", "unknown")}
