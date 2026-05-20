"""Agent ToolDispatcher routes prefixed names to a remote client."""

from typing import Any

import pytest

from lumid_data.agent.providers.base import ToolCall, ToolDef
from lumid_data.agent.runner import ToolDispatcher


class _StubRemote:
    def __init__(self, prefix: str = "remote_") -> None:
        self._prefix = prefix
        self.called: list[tuple[str, dict[str, Any]]] = []

    def owns(self, name: str) -> bool:
        return name.startswith(self._prefix)

    def tools(self) -> list[ToolDef]:
        return [ToolDef(name="remote_ohlc", description="bars", input_schema={})]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.called.append((name, arguments))
        return {"status_code": 200, "body": {"echo": arguments}}


@pytest.mark.asyncio
async def test_dispatcher_routes_prefixed_call_to_remote() -> None:
    remote = _StubRemote()
    tool = ToolDef(name="remote_ohlc", description="bars", input_schema={})
    dispatcher = ToolDispatcher(
        base_url="http://x",
        bearer=None,
        tools_by_name={tool.name: tool},
        remote_client=remote,
    )
    tc = ToolCall(call_id="c1", name="remote_ohlc", arguments={"symbol": "NVDA"})
    result = await dispatcher.call(tc)
    assert result == {"status_code": 200, "body": {"echo": {"symbol": "NVDA"}}}
    assert remote.called == [("remote_ohlc", {"symbol": "NVDA"})]


@pytest.mark.asyncio
async def test_dispatcher_unknown_tool_when_remote_doesnt_own() -> None:
    remote = _StubRemote()
    tool = ToolDef(name="get_db", description="db", input_schema={})
    dispatcher = ToolDispatcher(
        base_url="http://x",
        bearer=None,
        tools_by_name={tool.name: tool},
        remote_client=remote,
    )
    tc = ToolCall(call_id="c1", name="get_db", arguments={})
    result = await dispatcher.call(tc)
    # No route registered → unknown tool error, but no remote routing.
    assert "error" in result
    assert remote.called == []


@pytest.mark.asyncio
async def test_dispatcher_tool_not_in_catalog_short_circuits() -> None:
    remote = _StubRemote()
    dispatcher = ToolDispatcher(
        base_url="http://x",
        bearer=None,
        tools_by_name={},  # empty catalog
        remote_client=remote,
    )
    tc = ToolCall(call_id="c1", name="remote_ohlc", arguments={})
    result = await dispatcher.call(tc)
    assert "not available" in result["error"]
    assert remote.called == []
