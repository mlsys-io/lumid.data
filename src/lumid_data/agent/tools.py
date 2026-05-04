"""Build the agent's tool catalog from the FastAPI app's routes.

Goal: there is no second source-of-truth. Every CRUD endpoint a direct
client can hit is automatically a tool the agent can call. Adding a
new route gets the agent a new capability for free.

Strategy: walk ``app.routes``, skip the agent + health + docs surfaces,
and produce a :class:`ToolDef` per ``(method, path)`` with a schema
derived from the path / query parameters and the request-body model.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from .providers.base import ToolDef

_EXCLUDE_PATH_PREFIXES = ("/agent", "/mcp", "/healthz", "/docs", "/openapi", "/redoc")


def build_tool_catalog(app: FastAPI) -> list[ToolDef]:
    tools: list[ToolDef] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if any(route.path.startswith(p) for p in _EXCLUDE_PATH_PREFIXES):
            continue
        for method in sorted(route.methods or set()):
            if method in {"OPTIONS", "HEAD"}:
                continue
            tool_name = _tool_name(method, route.path)
            description = (route.description or route.summary or "").strip()
            if not description:
                description = f"{method} {route.path}"
            schema = _input_schema(route)
            tools.append(
                ToolDef(name=tool_name, description=description, input_schema=schema)
            )
    return tools


def _tool_name(method: str, path: str) -> str:
    cleaned = path.strip("/").replace("/", ".").replace("{", "").replace("}", "")
    cleaned = cleaned.replace(":path", "").replace("__", "_")
    return f"{method.lower()}_{cleaned}".replace("-", "_")[:64]


def _input_schema(route: APIRoute) -> dict[str, Any]:
    """Approximate JSON-schema for the tool's input.

    We don't try to be perfect — for v1 the agent learns the call shape
    from the description text. Future work: introspect Pydantic body
    models via ``route.body_field``.
    """
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for param in route.dependant.path_params:  # codespell:ignore dependant
        properties[param.name] = {"type": "string", "description": "path parameter"}
        required.append(param.name)
    for param in route.dependant.query_params:  # codespell:ignore dependant
        desc = (param.field_info.description or "").strip()
        properties[param.name] = {
            "type": "string",
            "description": f"query parameter ({desc})",
        }
        if getattr(param, "required", False):
            required.append(param.name)
    if route.body_field is not None:
        properties["body"] = {
            "type": "object",
            "description": "JSON request body",
        }
        required.append("body")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
    }
