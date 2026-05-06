"""Provider-agnostic tool-use loop driving an :class:`LLMAdapter`.

The runner is **stateless** with respect to the LLM session — each
``run()`` opens, drives, and closes a transcript. The transcript is
returned (and the caller persists it into ``agent_runs``).

Tool execution is by **HTTP roundtrip to the same lumid.data instance**
that's running the agent. The agent calls the same URLs a direct client
would, with the user's bearer token, so RBAC + audit are uniform.
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from .providers.base import (
    ChatMessage,
    LLMAdapter,
    ToolCall,
    ToolDef,
)
from .tools import _tool_name as _route_tool_name  # noqa: F401  (test seam)

logger = logging.getLogger(__name__)


@dataclass
class RunnerEvent:
    type: str  # "text" | "tool_call" | "tool_result" | "done" | "error"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    status: str  # "done" | "failed" | "aborted"
    final_text: str
    transcript: list[dict[str, Any]]
    steps: int
    tokens_in: int
    tokens_out: int
    error: str | None = None


@dataclass
class ToolDispatcher:
    """Maps tool calls back to HTTP requests against the lumid.data base URL."""

    base_url: str
    bearer: str | None
    timeout_sec: float = 30.0
    tools_by_name: dict[str, ToolDef] = field(default_factory=dict)
    routes_by_name: dict[str, tuple[str, str]] = field(default_factory=dict)
    # name -> (method, path-template)

    async def call(self, tc: ToolCall) -> dict[str, Any]:
        if tc.name not in self.routes_by_name:
            return {"error": f"unknown tool {tc.name!r}"}
        method, path_template = self.routes_by_name[tc.name]
        path = path_template
        body_payload = tc.arguments.get("body")
        params: dict[str, str] = {}
        for key, value in tc.arguments.items():
            if key == "body":
                continue
            placeholder = "{" + key + "}"
            if placeholder in path:
                path = path.replace(placeholder, str(value))
            else:
                params[key] = str(value)
        url = f"{self.base_url.rstrip('/')}{path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        timeout = httpx.Timeout(self.timeout_sec, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.request(
                method, url, params=params, headers=headers, json=body_payload
            )
        try:
            data: Any = resp.json()
        except ValueError:
            data = resp.text
        return {"status_code": resp.status_code, "body": data}


def build_dispatcher_from_app(
    app: Any, base_url: str, bearer: str | None
) -> ToolDispatcher:
    from fastapi.routing import APIRoute

    from .tools import _tool_name

    routes: dict[str, tuple[str, str]] = {}
    tools: dict[str, ToolDef] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method in {"OPTIONS", "HEAD"}:
                continue
            name = _tool_name(method, route.path)
            routes[name] = (method, route.path)
    return ToolDispatcher(
        base_url=base_url, bearer=bearer, routes_by_name=routes, tools_by_name=tools
    )


_DEFAULT_SYSTEM = (
    "You are the lumid.data agent. The user describes a data-management "
    "intent in natural language; you accomplish it by calling the "
    "registered tools. Each tool corresponds to a CRUD HTTP endpoint on "
    "the same service. Prefer reading before writing; explain what you "
    "did at the end."
)


async def run(
    *,
    adapter: LLMAdapter,
    tools: list[ToolDef],
    dispatcher: ToolDispatcher,
    goal: str,
    max_steps: int = 20,
    system: str | None = None,
) -> AsyncIterator[RunnerEvent]:
    """Drive the tool-use loop, yielding RunnerEvents as they happen."""
    transcript: list[dict[str, Any]] = []
    messages: list[ChatMessage] = [ChatMessage(role="user", content=goal)]
    final_text_parts: list[str] = []
    tokens_in = 0
    tokens_out = 0
    steps = 0
    status = "done"
    error: str | None = None

    dispatcher.tools_by_name = {t.name: t for t in tools}

    while steps < max_steps:
        steps += 1
        tool_calls: list[ToolCall] = []
        text_buf: list[str] = []
        try:
            async for ev in adapter.stream_chat(
                system=system or _DEFAULT_SYSTEM,
                messages=messages,
                tools=tools,
                max_tokens=4096,
            ):
                if ev.type == "text" and ev.text:
                    text_buf.append(ev.text)
                    yield RunnerEvent(type="text", payload={"text": ev.text})
                elif ev.type == "tool_call" and ev.tool_call is not None:
                    tool_calls.append(ev.tool_call)
                    yield RunnerEvent(
                        type="tool_call",
                        payload={
                            "name": ev.tool_call.name,
                            "arguments": ev.tool_call.arguments,
                            "call_id": ev.tool_call.call_id,
                        },
                    )
                elif ev.type == "input_tokens":
                    tokens_in += ev.tokens
                elif ev.type == "output_tokens":
                    tokens_out += ev.tokens
                elif ev.type == "error":
                    status = "failed"
                    error = ev.error
                    yield RunnerEvent(type="error", payload={"error": ev.error or ""})
                    return
        except Exception as exc:  # noqa: BLE001
            logger.exception("adapter stream failed")
            status = "failed"
            error = str(exc)
            yield RunnerEvent(type="error", payload={"error": error})
            return

        assistant_text = "".join(text_buf)
        if assistant_text:
            final_text_parts.append(assistant_text)
        transcript.append(
            {
                "step": steps,
                "assistant_text": assistant_text,
                "tool_calls": [
                    {
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "call_id": tc.call_id,
                    }
                    for tc in tool_calls
                ],
            }
        )

        if not tool_calls:
            break

        assistant_blocks: list[dict[str, Any]] = []
        if assistant_text:
            assistant_blocks.append({"type": "text", "text": assistant_text})
        for tc in tool_calls:
            assistant_blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.call_id,
                    "name": tc.name,
                    "input": tc.arguments,
                }
            )
        messages.append(ChatMessage(role="assistant", content=assistant_blocks))

        tool_result_blocks: list[dict[str, Any]] = []
        for tc in tool_calls:
            result = await dispatcher.call(tc)
            yield RunnerEvent(
                type="tool_result",
                payload={"call_id": tc.call_id, "result": result},
            )
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.call_id,
                    "content": json.dumps(result)[:8000],
                }
            )
        messages.append(ChatMessage(role="tool", content=tool_result_blocks))

    if steps >= max_steps:
        status = "aborted"
        error = "max_steps exhausted"

    yield RunnerEvent(
        type="done",
        payload={
            "status": status,
            "final_text": "\n".join(final_text_parts),
            "steps": steps,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "error": error,
            "transcript": transcript,
        },
    )
