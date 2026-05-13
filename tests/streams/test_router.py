"""Endpoint integration: register → set state → webhook push → DLQ replay.

Uses an in-memory SQLite DB and stubs the runner's deliver path so we
exercise the HTTP layer end-to-end without a real Postgres or S3.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lumid_data.db import Base
from lumid_data.server.routers import streams as streams_router
from lumid_data.server.state import AppState


class _StubRunner:
    def __init__(self) -> None:
        self.delivered: list[tuple[dict[str, Any], dict[str, Any], str]] = []

    async def deliver(self, sink, msg, source_id: str) -> None:
        self.delivered.append((sink, msg.payload, source_id))

    def spawn(self, source_id: str) -> None: ...

    async def stop(self, source_id: str) -> None: ...

    async def stop_all(self) -> None: ...


@pytest.fixture
async def app_state():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"lumid_data_meta": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    runner = _StubRunner()
    state = AppState(
        settings=None,  # type: ignore[arg-type]
        engine=engine,
        sessionmaker=sm,
        s3_cfg=None,  # type: ignore[arg-type]
        s3_client=None,
        audit=None,  # type: ignore[arg-type]
        stream_runner=runner,
    )
    yield state, runner
    await engine.dispose()


@pytest.fixture
def client(app_state):
    state, _ = app_state
    app = FastAPI()
    app.state.app_state = state
    app.include_router(streams_router.router)
    # Stub ensure_postgres_landing — sqlite can't run timescale DDL.
    streams_router.sinks.ensure_postgres_landing = AsyncMock()  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


def test_register_list_status_webhook(client, app_state) -> None:
    _, runner = app_state
    r = client.post(
        "/v1/streams",
        json={
            "name": "demo",
            "transport": "webhook",
            "config": {},
            "sink": {
                "kind": "postgres_table",
                "schema": "public",
                "table": "demo_events",
            },
        },
    )
    assert r.status_code == 201
    sid = r.json()["id"]
    assert sid.startswith("str-")

    r = client.get("/v1/streams")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # paused → 409 on push
    r = client.post(f"/v1/ingest/{sid}", json={"px": 100})
    assert r.status_code == 409

    # activate
    r = client.post(f"/v1/streams/{sid}/state", json={"state": "active"})
    assert r.status_code == 200

    # push delivers
    r = client.post(f"/v1/ingest/{sid}", json={"px": 100, "sym": "AAPL"})
    assert r.status_code == 200
    assert len(runner.delivered) == 1
    sink, payload, src_id = runner.delivered[0]
    assert sink["table"] == "demo_events"
    assert payload == {"px": 100, "sym": "AAPL"}
    assert src_id == sid

    r = client.get(f"/v1/streams/{sid}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "active"


def test_kafka_register_does_not_auto_start(client, app_state) -> None:
    r = client.post(
        "/v1/streams",
        json={
            "name": "ticks",
            "transport": "kafka",
            "config": {"topic": "ticks", "group_id": "g1"},
            "sink": {
                "kind": "postgres_table",
                "schema": "public",
                "table": "ticks_landing",
            },
        },
    )
    assert r.status_code == 201
    assert r.json()["state"] == "paused"


def test_register_rejects_postgres_sink_without_table(client) -> None:
    r = client.post(
        "/v1/streams",
        json={
            "name": "x",
            "transport": "webhook",
            "config": {},
            "sink": {"kind": "postgres_table", "schema": "public"},
        },
    )
    assert r.status_code == 400
