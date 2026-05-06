"""Per-source supervised asyncio task.

For active sources (kafka): one task per stream_sources row whose
state is 'active'. The task opens the source, iterates messages, writes
to the sink, and bumps stream_runs counters. On error: park the
message in stream_dlq, increment error_count, continue. On crash:
close_run(failed) and exponentially back off, then restart while state
remains 'active'.

Webhook and websocket sources are passive — runner ignores them; their
FastAPI handlers call ``deliver`` directly.
"""

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from . import registry, sinks
from .base import StreamMessage
from .kafka import KafkaSource

logger = logging.getLogger("lumid_data.streams.runner")


class StreamRunner:
    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        engine: AsyncEngine,
        s3_client: Any,
        kafka_bootstrap_default: str | None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._engine = engine
        self._s3_client = s3_client
        self._kafka_bootstrap_default = kafka_bootstrap_default
        self._tasks: dict[str, asyncio.Task] = {}

    async def start_all_active(self) -> None:
        async with self._sessionmaker() as session:
            for src in await registry.list_sources(session):
                if src.state == "active" and src.transport == "kafka":
                    self.spawn(src.id)

    def spawn(self, source_id: str) -> None:
        if source_id in self._tasks and not self._tasks[source_id].done():
            return
        self._tasks[source_id] = asyncio.create_task(
            self._supervise(source_id), name=f"stream-{source_id}"
        )

    async def stop(self, source_id: str) -> None:
        task = self._tasks.pop(source_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def stop_all(self) -> None:
        for source_id in list(self._tasks.keys()):
            await self.stop(source_id)

    async def deliver(
        self, sink_descriptor: dict[str, Any], msg: StreamMessage, source_id: str
    ) -> None:
        """Passive sources call this directly from their FastAPI handler."""
        ctx = sinks.SinkContext(engine=self._engine, s3_client=self._s3_client)
        try:
            await sinks.write(sink_descriptor, msg, ctx)
        except Exception as exc:
            logger.exception("sink write failed for source %s", source_id)
            async with self._sessionmaker() as session:
                await sinks.write_dlq(session, source_id, msg, repr(exc))
            raise

    async def _supervise(self, source_id: str) -> None:
        backoff = 1.0
        while True:
            try:
                await self._run_once(source_id)
                return  # source went inactive cleanly
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "stream %s crashed; backing off %.1fs", source_id, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _run_once(self, source_id: str) -> None:
        async with self._sessionmaker() as session:
            src = await registry.get(session, source_id)
            if src is None or src.state != "active" or src.transport != "kafka":
                return
            descriptor = src
            run = await registry.open_run(session, source_id)

        bootstrap = descriptor.config.get("bootstrap") or self._kafka_bootstrap_default
        if not bootstrap:
            async with self._sessionmaker() as session:
                await registry.close_run(
                    session,
                    run.id,
                    status="failed",
                    last_error="no kafka bootstrap configured",
                )
            return

        source = KafkaSource(
            name=descriptor.name,
            bootstrap=bootstrap,
            topic=descriptor.config["topic"],
            group_id=descriptor.config.get("group_id", f"lumid-data-{source_id}"),
            auto_offset_reset=descriptor.config.get("auto_offset_reset", "latest"),
        )
        await source.open()
        ctx = sinks.SinkContext(engine=self._engine, s3_client=self._s3_client)
        try:
            async for msg in source.stream():
                async with self._sessionmaker() as session:
                    fresh = await registry.get(session, source_id)
                    if fresh is None or fresh.state != "active":
                        return
                try:
                    await sinks.write(descriptor.sink, msg, ctx)
                    async with self._sessionmaker() as session:
                        await registry.bump_run(
                            session, run.id, processed=1, last_offset=msg.offset
                        )
                except Exception as exc:
                    logger.warning("sink write failed for %s: %r", source_id, exc)
                    async with self._sessionmaker() as session:
                        await sinks.write_dlq(session, source_id, msg, repr(exc))
                        await registry.bump_run(session, run.id, errors=1)
        finally:
            await source.close()
            async with self._sessionmaker() as session:
                await registry.close_run(session, run.id, status="stopped")
