"""Anthropic LLMAdapter — uses the Claude SDK with prompt caching.

The system prompt + tool catalog are cached (per the ``claude-api``
skill in this stack) so multi-step agent runs amortize the catalog
cost across tool-use iterations.
"""

from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

from .base import ChatMessage, LLMAdapter, StreamEvent, ToolCall, ToolDef


class AnthropicAdapter(LLMAdapter):
    name = "anthropic"

    def __init__(self, *, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)

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
        # Cache the (large, stable) system prompt + tool catalog so
        # subsequent tool-use turns reuse the prefix.
        cached_system = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        api_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema or {"type": "object", "properties": {}},
            }
            for t in tools
        ]
        if api_tools:
            api_tools[-1]["cache_control"] = {"type": "ephemeral"}

        api_messages = [_to_anthropic(m) for m in messages]

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": cached_system,
            "messages": api_messages,
        }
        if api_tools:
            kwargs["tools"] = api_tools

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                etype = getattr(event, "type", None)
                if etype == "content_block_delta":
                    delta: Any = getattr(event, "delta", None)
                    if (
                        delta is not None
                        and getattr(delta, "type", None) == "text_delta"
                    ):
                        yield StreamEvent(type="text", text=getattr(delta, "text", ""))
            final = await stream.get_final_message()
            for block in final.content:
                if getattr(block, "type", None) == "tool_use":
                    raw_block: Any = block
                    yield StreamEvent(
                        type="tool_call",
                        tool_call=ToolCall(
                            call_id=raw_block.id,
                            name=raw_block.name,
                            arguments=dict(raw_block.input or {}),
                        ),
                        raw=raw_block.model_dump(),
                    )
            usage = getattr(final, "usage", None)
            if usage is not None:
                yield StreamEvent(
                    type="input_tokens", tokens=int(usage.input_tokens or 0)
                )
                yield StreamEvent(
                    type="output_tokens", tokens=int(usage.output_tokens or 0)
                )
            yield StreamEvent(type="stop")


def _to_anthropic(msg: ChatMessage) -> dict[str, Any]:
    if msg.role == "tool":
        # tool results land as a user-role message with tool_result blocks.
        return {"role": "user", "content": msg.content}
    return {"role": msg.role, "content": msg.content}
