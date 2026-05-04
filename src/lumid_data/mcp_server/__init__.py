"""MCP server exposing the lumid.data CRUD surface as MCP tools."""

from .server import build_mcp_server

__all__ = ["build_mcp_server"]
