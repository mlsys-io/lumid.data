"""``LLMAdapter`` Protocol — provider-agnostic streaming chat with tool use.

Adapters yield a sequence of ``StreamEvent`` objects in the order the
underlying provider emits them. ``runner.py`` consumes these and drives
the tool-use loop without caring which provider is wired in.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

EventType = Literal[
    "text", "tool_call", "input_tokens", "output_tokens", "stop", "error"
]


@dataclass
class ToolDef:
    """Tool descriptor; provider-agnostic JSON shape."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """A tool the model wants the runner to execute."""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class StreamEvent:
    type: EventType
    text: str = ""
    tool_call: ToolCall | None = None
    tokens: int = 0
    error: str | None = None
    raw: Any = None


@dataclass
class ChatMessage:
    role: Literal["user", "assistant", "tool", "system"]
    content: Any  # str OR list of provider-shaped content blocks


class LLMAdapter(Protocol):
    """Minimal cross-provider adapter contract."""

    name: str
    model: str

    def stream_chat(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolDef],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]: ...

    async def aclose(self) -> None: ...
