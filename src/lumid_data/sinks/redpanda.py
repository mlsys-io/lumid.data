"""Redpanda (Kafka-protocol) producer + topic admin.

Stream-cadence sources land payloads on a per-source topic that
RisingWave consumes. We use ``confluent-kafka`` because it speaks the
same protocol Redpanda exposes.

The producer is connection-pooled per-instance and lazily flushed on
``close()``. ``ensure_topic`` is idempotent (no-op if the topic already
exists with the requested partition count).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RedpandaConfig:
    bootstrap_servers: str
    client_id: str = "lumid-data"
    default_partitions: int = 1
    default_replication: int = 1


class RedpandaProducer:
    """Wraps ``confluent_kafka.Producer`` with idempotent topic admin."""

    def __init__(self, cfg: RedpandaConfig) -> None:
        self._cfg = cfg
        self._producer: Any | None = None

    def connect(self) -> None:
        if self._producer is not None:
            return
        from confluent_kafka import Producer

        self._producer = Producer(
            {
                "bootstrap.servers": self._cfg.bootstrap_servers,
                "client.id": self._cfg.client_id,
                "enable.idempotence": True,
                "acks": "all",
            }
        )

    def close(self) -> None:
        if self._producer is None:
            return
        self._producer.flush(timeout=10)
        self._producer = None

    def ensure_topic(
        self,
        topic: str,
        partitions: int | None = None,
        replication: int | None = None,
    ) -> None:
        from confluent_kafka.admin import AdminClient, NewTopic

        admin = AdminClient({"bootstrap.servers": self._cfg.bootstrap_servers})
        existing = admin.list_topics(timeout=10).topics
        if topic in existing:
            return
        new = NewTopic(
            topic,
            num_partitions=partitions or self._cfg.default_partitions,
            replication_factor=replication or self._cfg.default_replication,
        )
        futures = admin.create_topics([new])
        future = futures[topic]
        future.result(timeout=15)
        logger.info("created topic %s on %s", topic, self._cfg.bootstrap_servers)

    def produce(
        self,
        topic: str,
        payload: bytes,
        key: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if self._producer is None:
            raise RuntimeError("RedpandaProducer not connected")
        kafka_headers = [(k, v.encode("utf-8")) for k, v in (headers or {}).items()]
        self._producer.produce(
            topic=topic,
            value=payload,
            key=key,
            headers=kafka_headers if kafka_headers else None,
        )
        self._producer.poll(0)

    def flush(self, timeout: float = 10.0) -> int:
        if self._producer is None:
            return 0
        return int(self._producer.flush(timeout=timeout))
