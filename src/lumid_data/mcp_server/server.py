"""MCP server exposing every CRUD endpoint as a tool.

We don't ship a separate tool registry. The MCP server walks the same
FastAPI app the rest of the service uses, derives the tool list with
``agent.tools.build_tool_catalog`` (so the agent and MCP catalogs are
identical by construction), and translates each ``call_tool`` into an
HTTP roundtrip back into the same lumid.data instance.

Mounted at ``/mcp`` via streamable HTTP transport.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from mcp.server.lowlevel import Server
from mcp.types import TextContent, Tool

from ..agent.tools import _tool_name, build_tool_catalog


def build_mcp_server(
    app: Any,
    *,
    base_url: str,
    name: str = "lumid.data",
) -> Server:
    """Construct an MCP Server registering each FastAPI route as a tool.

    If lifespan attaches a remote MCP client to ``app.state.app_state``
    (under attribute ``remote_mcp``), its discovered tools are also
    surfaced in ``list_tools`` and dispatched through the client on
    ``call_tool``. Lookup is dynamic so the catalog reflects whatever
    is current — the server builds at app-construction time but lifespan
    hasn't run yet.
    """
    server = Server(name)
    tool_catalog = build_tool_catalog(app)
    routes_by_name: dict[str, tuple[str, str]] = {}
    for route in _iter_api_routes(app):
        for method in route.methods or set():
            if method in {"OPTIONS", "HEAD"}:
                continue
            routes_by_name[_tool_name(method, route.path)] = (method, route.path)

    def _remote() -> Any:
        return getattr(getattr(app.state, "app_state", None), "remote_mcp", None)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
            )
            for t in tool_catalog
        ]
        remote = _remote()
        if remote is not None:
            tools.extend(
                Tool(
                    name=t.name,
                    description=t.description,
                    inputSchema=t.input_schema,
                )
                for t in remote.tools()
            )
        return tools

    caller: Callable[[str, dict[str, Any], str | None], Awaitable[dict[str, Any]]] = (
        _make_caller(routes_by_name, base_url)
    )

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[TextContent]:
        remote = _remote()
        if remote is not None and remote.owns(name):
            result = await remote.call(name, arguments or {})
            return [TextContent(type="text", text=json.dumps(result))]
        token = (arguments or {}).pop("_bearer", None) if arguments else None
        result = await caller(name, arguments or {}, token)
        return [TextContent(type="text", text=json.dumps(result))]

    return server


def _iter_api_routes(app: Any):
    from fastapi.routing import APIRoute

    for r in app.routes:
        if isinstance(r, APIRoute):
            yield r


def _make_caller(
    routes_by_name: dict[str, tuple[str, str]], base_url: str
) -> Callable[[str, dict[str, Any], str | None], Awaitable[dict[str, Any]]]:
    async def _call(
        name: str, args: dict[str, Any], bearer: str | None
    ) -> dict[str, Any]:
        if name not in routes_by_name:
            return {"error": f"unknown tool {name!r}"}
        method, path_template = routes_by_name[name]
        path = path_template
        body = args.pop("body", None)
        params: dict[str, str] = {}
        for key, value in args.items():
            placeholder = "{" + key + "}"
            if placeholder in path:
                path = path.replace(placeholder, str(value))
            else:
                params[key] = str(value)
        url = f"{base_url.rstrip('/')}{path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0)
        ) as client:
            resp = await client.request(
                method, url, params=params, headers=headers, json=body
            )
        try:
            data: Any = resp.json()
        except ValueError:
            data = resp.text
        return {"status_code": resp.status_code, "body": data}

    return _call
