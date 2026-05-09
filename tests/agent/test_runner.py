"""Agent runner behavior."""

from collections.abc import AsyncIterator

from lumid_data.agent.providers.base import (
    ChatMessage,
    StreamEvent,
    ToolCall,
    ToolDef,
)
from lumid_data.agent.runner import RunnerEvent, ToolDispatcher, run


class EarlyFinishAdapter:
    name = "test"
    model = "test"

    def __init__(self) -> None:
        self.calls: list[list[ChatMessage]] = []

    async def stream_chat(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolDef],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(messages.copy())
        if len(self.calls) == 1:
            yield StreamEvent(type="text", text="The answer is ready.")
            yield StreamEvent(type="stop")
            return
        if len(self.calls) == 2:
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    call_id="replay-1",
                    name="replay_retrieval_plan",
                    arguments={"plan": {"kind": "sql"}},
                ),
            )
            yield StreamEvent(type="stop")
            return
        yield StreamEvent(type="text", text="Replay completed.")
        yield StreamEvent(type="stop")

    async def aclose(self) -> None:
        return None


async def test_runner_forces_continuation_until_required_tool_succeeds() -> None:
    async def replay_tool(arguments: dict) -> dict:
        return {
            "run_id": "run-1",
            "materialized_uri": "s3://archive/run.jsonl",
            "signed_url": "http://signed/full-result",
            "output_format": "jsonl",
            "rowcount": 5,
            "size_bytes": 120,
            "replay_latency_ms": 7,
        }

    adapter = EarlyFinishAdapter()
    dispatcher = ToolDispatcher(base_url="http://test", bearer=None)
    dispatcher.local_tools["replay_retrieval_plan"] = replay_tool

    events: list[RunnerEvent] = [
        ev
        async for ev in run(
            adapter=adapter,
            tools=[
                ToolDef(
                    name="replay_retrieval_plan",
                    description="materialize retrieval result",
                )
            ],
            dispatcher=dispatcher,
            goal="retrieve AAPL fundamentals",
            max_steps=3,
            required_success_tools={"replay_retrieval_plan"},
            required_tools_message="Call replay_retrieval_plan before finishing.",
            tool_result_visibility="preview_stats",
        )
    ]

    assert len(adapter.calls) == 3
    assert adapter.calls[1][-1] == ChatMessage(
        role="user", content="Call replay_retrieval_plan before finishing."
    )
    assert any(
        ev.type == "tool_result"
        and ev.payload["name"] == "replay_retrieval_plan"
        and ev.payload["result"]["status_code"] == 200
        for ev in events
    )
    tool_message = adapter.calls[2][-1]
    assert tool_message.role == "tool"
    assert tool_message.content == [
        {
            "type": "tool_result",
            "tool_use_id": "replay-1",
            "content": (
                '{"status_code": 200, "body": {"run_id": "run-1", '
                '"output_format": "jsonl", "rowcount": 5, "size_bytes": 120, '
                '"replay_latency_ms": 7}}'
            ),
        }
    ]
    assert "s3://archive/run.jsonl" not in str(tool_message.content)
    assert "http://signed/full-result" not in str(tool_message.content)
    assert events[-1].type == "done"
    assert events[-1].payload["status"] == "done"
