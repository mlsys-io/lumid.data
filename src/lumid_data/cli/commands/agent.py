"""``lumid-data agent run "..."`` — drive /agent/v1 from the terminal."""

import json
import os
import sys

import typer

from ...sdk import Client, ClientError

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _client() -> Client:
    return Client(
        base_url=os.environ.get("LUMID_DATA_URL", "http://127.0.0.1:9100"),
    )


@app.command("run")
def run(
    goal: str = typer.Argument(..., help="natural-language intent"),
    max_steps: int = typer.Option(20, "--max-steps"),
    model: str | None = typer.Option(None, "--model"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    c = _client()
    try:
        for event, payload in c.agent_run(goal, max_steps=max_steps, model=model):
            if json_out:
                typer.echo(json.dumps({"event": event, "payload": payload}))
            elif event == "text":
                typer.echo(payload.get("text", ""), nl=False)
            elif event == "tool_call":
                name = payload.get("name", "?")
                typer.echo(typer.style(f"\n[tool] {name}", fg=typer.colors.CYAN))
            elif event == "tool_result":
                code = payload.get("result", {}).get("status_code", "?")
                typer.echo(typer.style(f"[tool result] {code}", fg=typer.colors.GREEN))
            elif event == "done":
                summary = (
                    f"\n\n[done] status={payload.get('status')} "
                    f"steps={payload.get('steps')} "
                    f"in/out={payload.get('tokens_in')}/{payload.get('tokens_out')}"
                )
                typer.echo(typer.style(summary, fg=typer.colors.BLUE))
            elif event == "error":
                typer.echo(typer.style(f"\n[error] {payload}", fg=typer.colors.RED))
                sys.exit(1)
    except ClientError as exc:
        typer.echo(f"agent run failed: {exc}")
        sys.exit(1)
