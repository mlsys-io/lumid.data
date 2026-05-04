"""OpenAI Chat Completions adapter (tool calls)."""

import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from .base import ChatMessage, LLMAdapter, StreamEvent, ToolCall, ToolDef


class OpenAIAdapter(LLMAdapter):
    name = "openai"

    def __init__(self, *, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    async def aclose(self) -> None:
        await self._client.close()

    async def stream_chat(  # type: ignore[override]
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolDef],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        api_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for msg in messages:
            api_messages.append(_to_openai(msg))
        api_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema
                    or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if api_tools:
            kwargs["tools"] = api_tools

        pending_calls: dict[int, dict[str, Any]] = {}
        usage_in = 0
        usage_out = 0
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield StreamEvent(type="text", text=delta.content)
                    for tc in delta.tool_calls or []:
                        idx = tc.index or 0
                        slot = pending_calls.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["name"] = tc.function.name
                            if tc.function.arguments:
                                slot["arguments"] += tc.function.arguments
                if chunk.usage:
                    usage_in = chunk.usage.prompt_tokens
                    usage_out = chunk.usage.completion_tokens
        except Exception as exc:
            yield StreamEvent(type="error", error=str(exc))
            return

        for slot in pending_calls.values():
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    call_id=slot["id"] or slot["name"],
                    name=slot["name"],
                    arguments=args,
                ),
            )

        if usage_in:
            yield StreamEvent(type="input_tokens", tokens=usage_in)
        if usage_out:
            yield StreamEvent(type="output_tokens", tokens=usage_out)
        yield StreamEvent(type="stop")


def _to_openai(msg: ChatMessage) -> dict[str, Any]:
    if msg.role == "tool":
        return (
            msg.content
            if isinstance(msg.content, dict)
            else {
                "role": "tool",
                "content": str(msg.content),
            }
        )
    return {"role": msg.role, "content": msg.content}
