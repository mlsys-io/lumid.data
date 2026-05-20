"""Optional MCP-over-SSE client for a remote read-only data source.

When ``LUMID_DATA_REMOTE_MCP_URL`` is set, lumid.data opens an MCP
client session against that URL at lifespan startup and re-exposes the
remote's tools through both the agent tool catalog (so ``/agent/v1``
can call them) and lumid.data's own ``/mcp`` server (so MCP hosts that
connect to lumid.data can use them transitively).

The integration is generic: any MCP-over-SSE server with bearer-token
auth works.
"""

from .client import RemoteMCPClient

__all__ = ["RemoteMCPClient"]
