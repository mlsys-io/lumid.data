"""SDK retrieval wrapper tests."""

from collections.abc import Iterator

import pytest

from lumid_data.sdk.core import Client, ClientError


def _replay_event(uri: str) -> tuple[str, dict]:
    run_id = uri.rsplit("/", 2)[-2]
    return (
        "tool_result",
        {
            "name": "replay_retrieval_plan",
            "result": {
                "status_code": 200,
                "body": {
                    "run_id": run_id,
                    "materialized_uri": uri,
                    "signed_url": f"http://signed/{run_id}",
                    "output_format": "jsonl",
                    "access_chain": [],
                    "rowcount": 1,
                    "size_bytes": 10,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "steps_taken": 0,
                    "replay_latency_ms": 1,
                    "transcript_url": "http://runs/run",
                },
            },
        },
    )


def test_retrieve_returns_last_successful_replay(monkeypatch) -> None:
    client = Client(base_url="http://test")

    def fake_agent_run(*_args, **_kwargs) -> Iterator[tuple[str, dict]]:
        yield _replay_event("s3://bucket/retrievals/run-1/result.jsonl")
        yield (
            "tool_result",
            {
                "name": "post_sql_v1_count",
                "result": {"status_code": 200, "body": {"rows": [{"count": 1}]}},
            },
        )
        yield _replay_event("s3://bucket/retrievals/run-2/result.jsonl")
        yield ("done", {"tokens_in": 10, "tokens_out": 20, "steps": 3})

    monkeypatch.setattr(client, "agent_run", fake_agent_run)

    result = client.retrieve("show data")

    assert result.run_id == "run-2"
    assert result.materialized_uri == "s3://bucket/retrievals/run-2/result.jsonl"
    assert result.tokens_in == 10
    assert result.tokens_out == 20
    assert result.steps_taken == 3


def test_retrieve_reports_agent_done_without_replay(monkeypatch) -> None:
    client = Client(base_url="http://test")

    def fake_agent_run(*_args, **_kwargs) -> Iterator[tuple[str, dict]]:
        yield (
            "done",
            {
                "status": "aborted",
                "error": "max_steps exhausted",
                "steps": 20,
                "final_text": "I did not materialize anything.",
            },
        )

    monkeypatch.setattr(client, "agent_run", fake_agent_run)

    with pytest.raises(ClientError) as exc_info:
        client.retrieve("show data")

    message = str(exc_info.value)
    assert "max_steps exhausted" in message
    assert "I did not materialize anything" in message
