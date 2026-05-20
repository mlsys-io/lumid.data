"""E2E: real LLM + real remote MCP, targeted analytical workload.

Subject was picked by hand via a survey run against the warehouse;
this test pins it so the question shape is deterministic. Skipped
unless ``LUMID_DATA_REMOTE_MCP_URL`` and ``LUMID_DATA_LLM_API_KEY``
are set in ``.env``. CI ignores ``tests/e2e``.
"""

import json
import os
import re

import pytest
from dotenv import load_dotenv

from lumid_data.agent import make_adapter
from lumid_data.agent import run as run_agent
from lumid_data.agent.runner import RunnerEvent, ToolDispatcher
from lumid_data.remote import RemoteMCPClient
from lumid_data.server.config import load_settings

load_dotenv()

pytestmark = pytest.mark.skipif(
    not (
        os.getenv("LUMID_DATA_REMOTE_MCP_URL") and os.getenv("LUMID_DATA_LLM_API_KEY")
    ),
    reason="set LUMID_DATA_REMOTE_MCP_URL and LUMID_DATA_LLM_API_KEY in .env",
)


@pytest.fixture
async def remote():
    settings = load_settings()
    c = RemoteMCPClient(
        url=settings.remote_mcp_url,  # type: ignore[arg-type]
        token=settings.remote_mcp_token,
        prefix=settings.remote_mcp_prefix,
    )
    await c.aopen()
    try:
        yield c
    finally:
        await c.aclose()


@pytest.fixture
async def adapter():
    settings = load_settings()
    a = make_adapter(
        settings.llm_provider,
        model=settings.llm_model,
        api_key=settings.llm_api_key,  # type: ignore[arg-type]
        base_url=settings.llm_base_url,
    )
    try:
        yield a
    finally:
        await a.aclose()


_DEFAULT_SYSTEM = (
    "You are an evaluation harness. Answer the user's question by calling "
    "the available remote tools. Do not invent numbers — every claim must "
    "come from a tool result you fetched in this session."
)


async def _drive(
    *,
    adapter,
    remote: RemoteMCPClient,
    goal: str,
    max_steps: int = 6,
    system: str = _DEFAULT_SYSTEM,
) -> tuple[list[RunnerEvent], list[dict]]:
    """Run the agent, return events + successful tool_result payloads."""
    tools = remote.tools()
    assert tools, "remote MCP exposed no tools"
    dispatcher = ToolDispatcher(
        base_url="http://unused",
        bearer=None,
        remote_client=remote,
    )
    events: list[RunnerEvent] = []
    tool_results: list[dict] = []
    async for ev in run_agent(
        adapter=adapter,
        tools=tools,
        dispatcher=dispatcher,
        goal=goal,
        max_steps=max_steps,
        system=system,
        tool_result_visibility="full",
    ):
        events.append(ev)
        if ev.type == "tool_result":
            tool_results.append(ev.payload)
        if ev.type == "text" and ev.payload.get("text"):
            print(ev.payload["text"], end="", flush=True)
        elif ev.type == "tool_call":
            print(
                f"\n[tool_call] {ev.payload['name']} args={ev.payload['arguments']}",
                flush=True,
            )
        elif ev.type == "tool_result":
            body = ev.payload.get("result", {}).get("body")
            preview = json.dumps(body)[:240] if body is not None else "<None>"
            print(f"\n[tool_result] {ev.payload['name']} -> {preview}", flush=True)
        elif ev.type == "done":
            print(
                f"\n[done] status={ev.payload['status']} steps={ev.payload['steps']}",
                flush=True,
            )
    return events, tool_results


async def test_agent_analyzes_nvda_pre_earnings_rally(remote, adapter) -> None:
    """Agent must explain NVDA's +11% rally (2026-05-06 → 2026-05-18) into
    earnings on 2026-05-20: fetch price + catalysts, synthesize a narrative."""
    events, tool_results = await _drive(
        adapter=adapter,
        remote=remote,
        goal=(
            "Why did NVDA rally roughly 11% from 2026-05-06 to 2026-05-18, "
            "heading into its earnings on 2026-05-20?"
        ),
        max_steps=8,
    )

    tool_calls = [ev for ev in events if ev.type == "tool_call"]
    names_called = [tc.payload["name"] for tc in tool_calls]
    distinct = {tc.payload["name"] for tc in tool_calls}
    assert (
        len(tool_calls) >= 2
    ), f"analytical workload should call ≥2 tools; got {names_called}"
    assert (
        len(distinct) >= 2
    ), f"analytical workload should call ≥2 distinct tools; got {names_called}"
    assert all(
        n.startswith(remote.prefix) for n in names_called
    ), f"non-remote tool was called: {names_called}"
    assert any(
        "ohlc" in n for n in names_called
    ), f"expected an ohlc tool call; got {names_called}"
    catalyst_tools = ("news_for_symbol", "price_target", "holders_top", "fundamentals")
    assert any(
        any(c in n for c in catalyst_tools) for n in names_called
    ), f"expected a catalyst-style tool; got {names_called}"

    successes = [tr for tr in tool_results if tr["result"].get("status_code") == 200]
    statuses = [tr["result"].get("status_code") for tr in tool_results]
    assert len(successes) >= 2, f"expected ≥2 successful tool results; got {statuses}"

    done = [ev for ev in events if ev.type == "done"]
    assert done and done[0].payload["status"] == "done"
    final_text = done[0].payload["final_text"]
    assert (
        len(final_text) >= 500
    ), f"analytical answer too short ({len(final_text)} chars): {final_text!r}"

    grounded = bool(
        re.search(r"[-+]?\d+(?:\.\d+)?\s?%", final_text)
        or re.search(r"\d{4}-\d{2}-\d{2}", final_text)
        or re.search(r"\$\s?\d", final_text)
    )
    assert grounded, f"no data-grounded tokens (%/date/$): {final_text!r}"

    assert (
        "NVDA" in final_text.upper()
    ), f"final_text doesn't mention NVDA: {final_text!r}"
    assert (
        "earnings" in final_text.lower()
    ), f"final_text doesn't mention the earnings catalyst: {final_text!r}"
