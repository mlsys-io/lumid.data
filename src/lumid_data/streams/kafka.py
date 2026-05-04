"""Kafka active source via aiokafka.

config: {"bootstrap": "redpanda:9092", "topic": "...", "group_id": "..."}.
The bootstrap address falls back to ``Settings.kafka_bootstrap`` when
the descriptor leaves it unset, so most descriptors only need a topic.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from .base import StreamMessage

logger = logging.getLogger("lumid_data.streams.kafka")


class KafkaSource:
    def __init__(
        self,
        name: str,
        bootstrap: str,
        topic: str,
        group_id: str,
        auto_offset_reset: str = "latest",
    ) -> None:
        self.name = name
        self._bootstrap = bootstrap
        self._topic = topic
        self._group_id = group_id
        self._auto_offset_reset = auto_offset_reset
        self._consumer: Any = None

    async def open(self) -> None:
        from aiokafka import AIOKafkaConsumer

        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap,
            group_id=self._group_id,
            auto_offset_reset=self._auto_offset_reset,
            enable_auto_commit=True,
        )
        await self._consumer.start()

    async def close(self) -> None:
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None

    async def stream(self) -> AsyncIterator[StreamMessage]:
        if self._consumer is None:
            raise RuntimeError("KafkaSource not opened")
        async for record in self._consumer:
            try:
                payload = json.loads(record.value.decode())
                if not isinstance(payload, dict):
                    payload = {"value": payload}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"raw": record.value.hex()}
            headers = (
                {k: v.decode("utf-8", errors="replace") for k, v in record.headers}
                if record.headers
                else {}
            )
            yield StreamMessage(
                payload=payload,
                headers=headers,
                offset={
                    "topic": record.topic,
                    "partition": record.partition,
                    "offset": record.offset,
                },
            )
