"""Streaming ingestion: webhook, websocket, kafka → postgres / S3 / DLQ.

One module per transport, one module per sink; per-source asyncio
tasks are supervised by the runner.
"""

from .base import StreamMessage, StreamSource
from .runner import StreamRunner

__all__ = ["StreamMessage", "StreamSource", "StreamRunner"]
