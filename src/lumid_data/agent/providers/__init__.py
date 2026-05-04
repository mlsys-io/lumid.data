"""LLM provider adapters."""

from ..providers.base import (
    ChatMessage,
    LLMAdapter,
    StreamEvent,
    ToolCall,
    ToolDef,
)


def make_adapter(
    provider: str,
    *,
    model: str,
    api_key: str,
    base_url: str | None = None,
) -> LLMAdapter:
    if provider == "anthropic":
        from .anthropic import AnthropicAdapter

        return AnthropicAdapter(model=model, api_key=api_key, base_url=base_url)
    if provider == "openai":
        from .openai import OpenAIAdapter

        return OpenAIAdapter(model=model, api_key=api_key, base_url=base_url)
    if provider == "openai_compat":
        from .openai_compat import OpenAICompatAdapter

        return OpenAICompatAdapter(model=model, api_key=api_key, base_url=base_url)
    raise ValueError(f"unknown LLM provider: {provider!r}")


__all__ = [
    "ChatMessage",
    "LLMAdapter",
    "StreamEvent",
    "ToolCall",
    "ToolDef",
    "make_adapter",
]
