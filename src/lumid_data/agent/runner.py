"""Provider-agnostic tool-use loop driving an :class:`LLMAdapter`.

The runner is **stateless** with respect to the LLM session — each
``run()`` opens, drives, and closes a transcript. The transcript is
returned (and the caller persists it into ``agent_runs``).

Tool execution is either a local deterministic handler or an HTTP
roundtrip to the same lumid.data instance that's running the agent.
HTTP tools call the same URLs a direct client would, with the user's
bearer token, so RBAC + audit are uniform.
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi.encoders import jsonable_encoder

from .providers.base import (
    ChatMessage,
    LLMAdapter,
    ToolCall,
    ToolDef,
)
from .tools import _tool_name as _route_tool_name  # noqa: F401  (test seam)

logger = logging.getLogger(__name__)

LocalToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


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
    local_tools: dict[str, LocalToolHandler] = field(default_factory=dict)
    remote_client: Any = None  # ``RemoteMCPClient`` when wired
    # name -> (method, path-template)

    async def call(self, tc: ToolCall) -> dict[str, Any]:
        if tc.name not in self.tools_by_name:
            return {"error": f"tool {tc.name!r} is not available in this run"}
        if tc.name in self.local_tools:
            try:
                data = await self.local_tools[tc.name](tc.arguments)
            except Exception as exc:  # noqa: BLE001
                logger.exception("local tool %s failed", tc.name)
                return {"status_code": 500, "body": {"error": str(exc)}}
            return {"status_code": 200, "body": data}
        if self.remote_client is not None and self.remote_client.owns(tc.name):
            return await self.remote_client.call(tc.name, tc.arguments)
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
            response_body: Any = resp.json()
        except ValueError:
            response_body = resp.text
        return {"status_code": resp.status_code, "body": response_body}


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
    required_success_tools: set[str] | None = None,
    required_tools_message: str | None = None,
    tool_result_visibility: str = "full",
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
    successful_tools: set[str] = set()
    completed = False

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
            missing = (required_success_tools or set()) - successful_tools
            if not missing:
                completed = True
                break
            prompt = required_tools_message or (
                "You must call the required tool(s) before finishing: "
                f"{', '.join(sorted(missing))}."
            )
            messages.append(ChatMessage(role="user", content=prompt))
            continue

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
            if result.get("status_code") and int(result["status_code"]) < 300:
                successful_tools.add(tc.name)
            yield RunnerEvent(
                type="tool_result",
                payload={"call_id": tc.call_id, "name": tc.name, "result": result},
            )
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.call_id,
                    "content": _tool_result_content_for_model(
                        tc, result, visibility=tool_result_visibility
                    ),
                }
            )
        messages.append(ChatMessage(role="tool", content=tool_result_blocks))

    if not completed and steps >= max_steps:
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


def _tool_result_content_for_model(
    tc: ToolCall, result: dict[str, Any], *, visibility: str
) -> str:
    if visibility == "full":
        return json.dumps(jsonable_encoder(result))[:8000]
    if visibility != "preview_stats":
        return (
            "Tool call completed. Result content is not available "
            "in the model context."
        )
    sanitized: dict[str, Any] = {}
    if "status_code" in result:
        sanitized["status_code"] = result["status_code"]
    if "error" in result:
        sanitized["error"] = result["error"]
        return json.dumps(sanitized)[:8000]
    body = result.get("body")
    if not isinstance(body, dict):
        sanitized["body"] = {"message": "result body withheld"}
        return json.dumps(sanitized)[:8000]
    if tc.name == "replay_retrieval_plan":
        sanitized["body"] = {
            key: body[key]
            for key in (
                "run_id",
                "output_format",
                "rowcount",
                "size_bytes",
                "replay_latency_ms",
            )
            if key in body
        }
    elif tc.name in {
        "get_schema_cards",
        "post_sql_v1_explain",
        "post_sql_v1_count",
        "post_sql_v1_sample",
        "get_storage_v1_list_bucket",
        "get_storage_v1_stat_bucket_path",
    }:
        sanitized["body"] = body
    else:
        sanitized["body"] = {
            "error": (
                "full result content is not available in this agent run; "
                "only preview and stats tools are allowed"
            )
        }
    return json.dumps(jsonable_encoder(sanitized))[:8000]
