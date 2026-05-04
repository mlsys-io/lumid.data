"""Streaming ingestion: webhook, websocket, kafka → postgres / S3 / DLQ.

Mirrors fin_datalake's adapter pattern (one module per transport, one
module per sink) but stays inside the lumid.data process — no Prefect /
Ray / Delta. Per-source asyncio tasks are supervised by the runner.
"""

from .base import StreamMessage, StreamSource
from .runner import StreamRunner

__all__ = ["StreamMessage", "StreamSource", "StreamRunner"]
