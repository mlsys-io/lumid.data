"""Stream-source protocol and shared dataclasses.

A ``StreamMessage`` is the unit that flows from any transport to any
sink. ``offset`` is opaque per-transport (kafka: {topic, partition,
offset}; websocket: monotonic counter; webhook: ingest_id).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class StreamMessage:
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    offset: dict[str, Any] = field(default_factory=dict)


class StreamSource(Protocol):
    """Active source: the runner pulls messages from it.

    Webhook + websocket sources are *passive* (delivered by FastAPI
    handlers) so they don't implement this protocol — they call the
    sink directly.
    """

    name: str

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    def stream(self) -> AsyncIterator[StreamMessage]: ...
