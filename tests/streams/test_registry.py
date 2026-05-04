"""Registry: register/list/state-transition behaviour against an in-memory db."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lumid_data.db import Base
from lumid_data.streams import registry


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"lumid_data_meta": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_register_and_list(session) -> None:
    row = await registry.register(
        session,
        name="demo",
        transport="webhook",
        config={},
        sink={"kind": "postgres_table", "schema": "public", "table": "ev"},
        created_by="alice",
    )
    assert row.id.startswith("str-")
    assert row.state == "paused"
    rows = await registry.list_sources(session)
    assert len(rows) == 1


async def test_unknown_transport_rejected(session) -> None:
    with pytest.raises(ValueError):
        await registry.register(
            session,
            name="x",
            transport="ftp",
            config={},
            sink={},
            created_by="x",
        )


async def test_state_transitions(session) -> None:
    src = await registry.register(
        session,
        name="d",
        transport="webhook",
        config={},
        sink={"kind": "postgres_table", "table": "t"},
        created_by="x",
    )
    out = await registry.set_state(session, src.id, "active")
    assert out is not None and out.state == "active"
    with pytest.raises(ValueError):
        await registry.set_state(session, src.id, "running")
