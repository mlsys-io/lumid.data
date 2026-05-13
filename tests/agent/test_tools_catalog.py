"""Tool-catalog generation from a FastAPI app."""

from fastapi import FastAPI
from pydantic import BaseModel

from lumid_data.agent.providers.base import ToolCall, ToolDef
from lumid_data.agent.runner import ToolDispatcher
from lumid_data.agent.tools import build_tool_catalog


class TypedToolResult(BaseModel):
    value: int


def test_routes_become_tools_with_per_method_names() -> None:
    app = FastAPI()

    @app.get("/custom/v1/users", description="list users")
    async def list_users() -> list:
        return []

    @app.post("/custom/v1/users", description="create user")
    async def create_user(body: dict) -> dict:
        return {}

    tools = build_tool_catalog(app)
    names = {t.name for t in tools}
    assert "get_custom_v1_users" in names
    assert "post_custom_v1_users" in names
    assert all(t.input_schema for t in tools)


def test_excluded_paths_are_skipped() -> None:
    app = FastAPI()

    @app.get("/agent/v1")
    async def agent_route() -> dict:
        return {}

    @app.get("/healthz")
    async def health() -> dict:
        return {}

    @app.get("/custom/v1/x")
    async def x() -> list:
        return []

    tools = build_tool_catalog(app)
    names = {t.name for t in tools}
    assert "get_custom_v1_x" in names
    assert not any("agent" in n for n in names)
    assert not any("health" in n for n in names)


def test_path_param_is_required() -> None:
    app = FastAPI()

    @app.get("/custom/v1/users/{user_id}")
    async def get_user(user_id: str) -> dict:
        return {}

    tools = build_tool_catalog(app)
    user_tool = next(t for t in tools if "users" in t.name)
    assert "user_id" in user_tool.input_schema["required"]


async def test_local_tool_dispatch_preserves_typed_result() -> None:
    async def typed_tool(arguments: dict) -> TypedToolResult:
        return TypedToolResult(value=int(arguments["value"]))

    dispatcher = ToolDispatcher(base_url="http://test", bearer=None)
    dispatcher.local_tools["typed_tool"] = typed_tool
    dispatcher.tools_by_name["typed_tool"] = ToolDef(
        name="typed_tool", description="typed test tool"
    )

    result = await dispatcher.call(
        ToolCall(
            call_id="call-1",
            name="typed_tool",
            arguments={"value": "7"},
        )
    )

    assert result["status_code"] == 200
    assert result["body"] == TypedToolResult(value=7)


async def test_dispatcher_rejects_tools_not_available_in_run() -> None:
    dispatcher = ToolDispatcher(
        base_url="http://test",
        bearer=None,
        routes_by_name={"post_sql_v1": ("POST", "/sql/v1")},
        tools_by_name={
            "replay_retrieval_plan": ToolDef(
                name="replay_retrieval_plan", description="safe retrieval replay"
            )
        },
    )

    result = await dispatcher.call(
        ToolCall(
            call_id="call-1",
            name="post_sql_v1",
            arguments={"body": {"query": "SELECT * FROM secret"}},
        )
    )

    assert result == {"error": "tool 'post_sql_v1' is not available in this run"}
