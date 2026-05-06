"""Tests for the agent-plan JSON parser.

The parser is the contract between LLM output and the typed pipeline. It
must accept fenced JSON, bare JSON, and reject malformed payloads cleanly
so the router can either retry or surface a 502.
"""

import pytest

from lumid_data.retrieve.planner import PlannerError, parse_retrieval_plan


class TestParseRetrievalPlan:
    def test_bare_json(self):
        text = (
            '{"plan": [{"op": "sql", "query": "SELECT 1"}], '
            '"expected_rowcount_or_size": 1, "rationale": "trivial"}'
        )
        plan = parse_retrieval_plan(text)
        assert plan.expected_rowcount_or_size == 1
        assert plan.rationale == "trivial"
        assert len(plan.plan) == 1
        assert plan.plan[0].op == "sql"

    def test_fenced_json(self):
        text = (
            "Here is the plan:\n```json\n"
            '{"plan": [{"op": "sql", "query": "SELECT 1"}]}\n'
            "```\nDone."
        )
        plan = parse_retrieval_plan(text)
        assert len(plan.plan) == 1

    def test_bare_fence_no_lang(self):
        text = '```\n{"plan": [{"op": "sql", "query": "SELECT 1"}]}\n```'
        plan = parse_retrieval_plan(text)
        assert len(plan.plan) == 1

    def test_storage_get_op(self):
        text = '{"plan": [{"op": "storage_get", "bucket": "b", "key": "k.txt"}]}'
        plan = parse_retrieval_plan(text)
        assert len(plan.plan) == 1
        assert plan.plan[0].op == "storage_get"
        assert plan.plan[0].bucket == "b"
        assert plan.plan[0].key == "k.txt"

    def test_mixed_ops(self):
        text = (
            '{"plan": ['
            '{"op": "sql", "query": "SELECT id FROM t"},'
            '{"op": "storage_get", "bucket": "b", "key": "k"}'
            "]}"
        )
        plan = parse_retrieval_plan(text)
        assert len(plan.plan) == 2

    def test_falls_back_to_brace_search_when_no_fence(self):
        text = (
            "Some thinking text. Plan:\n"
            '{"plan": [{"op": "sql", "query": "SELECT 1"}]}\n'
            "Trailing prose."
        )
        plan = parse_retrieval_plan(text)
        assert len(plan.plan) == 1

    def test_empty_text_raises(self):
        with pytest.raises(PlannerError, match="empty final_text"):
            parse_retrieval_plan("")

    def test_no_json_object_raises(self):
        with pytest.raises(PlannerError, match="no JSON object found"):
            parse_retrieval_plan("just prose, no JSON anywhere")

    def test_malformed_json_raises(self):
        with pytest.raises(PlannerError, match="malformed"):
            parse_retrieval_plan('{"plan": [missing-quotes]}')

    def test_schema_violation_raises(self):
        # plan must be a list — passing a string fails Pydantic validation
        with pytest.raises(PlannerError, match="schema validation"):
            parse_retrieval_plan('{"plan": "not a list"}')

    def test_unknown_op_raises(self):
        with pytest.raises(PlannerError, match="schema validation"):
            parse_retrieval_plan(
                '{"plan": [{"op": "delete", "query": "DROP TABLE x"}]}'
            )
