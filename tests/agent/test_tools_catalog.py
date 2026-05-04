"""Tool-catalog generation from a FastAPI app."""

from fastapi import FastAPI

from lumid_data.agent.tools import build_tool_catalog


def test_routes_become_tools_with_per_method_names() -> None:
    app = FastAPI()

    @app.get("/db/v1/users", description="list users")
    async def list_users() -> list:
        return []

    @app.post("/db/v1/users", description="create user")
    async def create_user(body: dict) -> dict:
        return {}

    tools = build_tool_catalog(app)
    names = {t.name for t in tools}
    assert "get_db.v1.users" in names
    assert "post_db.v1.users" in names
    assert all(t.input_schema for t in tools)


def test_excluded_paths_are_skipped() -> None:
    app = FastAPI()

    @app.get("/agent/v1")
    async def agent_route() -> dict:
        return {}

    @app.get("/healthz")
    async def health() -> dict:
        return {}

    @app.get("/db/v1/x")
    async def x() -> list:
        return []

    tools = build_tool_catalog(app)
    names = {t.name for t in tools}
    assert "get_db.v1.x" in names
    assert not any("agent" in n for n in names)
    assert not any("health" in n for n in names)


def test_path_param_is_required() -> None:
    app = FastAPI()

    @app.get("/db/v1/users/{user_id}")
    async def get_user(user_id: str) -> dict:
        return {}

    tools = build_tool_catalog(app)
    user_tool = next(t for t in tools if "users" in t.name)
    assert "user_id" in user_tool.input_schema["required"]
