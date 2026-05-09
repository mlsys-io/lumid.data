"""Agent skill registry tests."""

import pytest

from lumid_data.server.skills import (
    UnknownSkillError,
    render_skill_prompt,
    skill_required_success_tools,
    skill_required_tools_message,
    skill_tool_allowlist,
    skill_names,
    skill_tool_result_visibility,
)


def test_data_retrieval_skill_is_registered() -> None:
    assert "data_retrieval" in skill_names()
    prompt = render_skill_prompt(["data_retrieval"])
    assert "get_schema_cards first" in prompt
    assert "replay_retrieval_plan" in prompt
    assert "object bytes" in prompt


def test_data_retrieval_skill_allows_only_preview_stats_and_replay_tools() -> None:
    tools = skill_tool_allowlist(["data_retrieval"])
    assert tools is not None
    assert "get_schema_cards" in tools
    assert "replay_retrieval_plan" in tools
    assert "post_sql_v1_sample" in tools
    assert "post_sql_v1" not in tools
    assert "get_storage_v1_object_bucket_path" not in tools


def test_data_retrieval_skill_requires_replay_success() -> None:
    assert skill_required_success_tools(["data_retrieval"]) == {
        "replay_retrieval_plan"
    }
    message = skill_required_tools_message(["data_retrieval"])
    assert message is not None
    assert "replay_retrieval_plan" in message
    assert "Do not answer from SQL rows" in message
    assert skill_tool_result_visibility(["data_retrieval"]) == "preview_stats"


def test_unknown_skill_raises() -> None:
    with pytest.raises(UnknownSkillError, match="unknown skills: missing"):
        render_skill_prompt(["missing"])
