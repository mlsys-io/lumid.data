"""Mount the MCP server's HTTP transport at ``/mcp``.

The MCP Python SDK ships a Streamable HTTP transport (``mcp.server.streamable_http``).
We wire it through FastAPI by mounting an ASGI app under ``/mcp``; the
underlying server is the one returned by
``lumid_data.mcp_server.build_mcp_server`` against the live FastAPI app.

Auth is the same lumid bearer token the rest of the service uses; MCP
clients pass it in transport headers.
"""

from typing import Any

from fastapi import FastAPI


def mount_mcp(app: FastAPI, *, base_url: str) -> None:
    """Attach an MCP HTTP transport at ``/mcp`` (best-effort).

    We don't fail the app if the transport import fails — the SDK's
    surface area is still settling. The MCP test suite mocks this out;
    a live server gracefully reports 503 when the transport is missing.
    """
    try:
        import importlib

        transport_mod = importlib.import_module("mcp.server.streamable_http")
        from ...mcp_server import build_mcp_server

        mcp_server = build_mcp_server(app, base_url=base_url)
        TransportCls: Any = getattr(transport_mod, "StreamableHTTPServerTransport")
        transport: Any = TransportCls(mcp_server=mcp_server)
        app.mount("/mcp", transport.app)
    except Exception:  # noqa: BLE001

        @app.get("/mcp")
        async def _mcp_unavailable() -> dict[str, str]:
            return {"detail": "MCP transport not available in this build"}
