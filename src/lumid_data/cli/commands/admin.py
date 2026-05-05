"""``lumid-data admin`` — read views over audit_log + agent_runs."""

import json
import os
import sys

import httpx
import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _base_url() -> str:
    return os.environ.get("LUMID_DATA_URL", "http://127.0.0.1:9100").rstrip("/")


@app.command("audit")
def audit(
    surface: str | None = None,
    op: str | None = None,
    limit: int = 50,
) -> None:
    params: dict[str, str] = {"limit": str(limit)}
    if surface:
        params["surface"] = surface
    if op:
        params["op"] = op
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{_base_url()}/v1/admin/audit", params=params)
    if r.status_code >= 300:
        typer.echo(f"audit failed ({r.status_code}): {r.text}")
        sys.exit(1)
    typer.echo(json.dumps(r.json(), indent=2, default=str))


@app.command("runs")
def runs(limit: int = 50) -> None:
    params: dict[str, str] = {"limit": str(limit)}
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{_base_url()}/v1/admin/runs", params=params)
    if r.status_code >= 300:
        typer.echo(f"runs failed ({r.status_code}): {r.text}")
        sys.exit(1)
    typer.echo(json.dumps(r.json(), indent=2, default=str))


@app.command("run")
def run(run_id: str) -> None:
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{_base_url()}/v1/admin/runs/{run_id}")
    if r.status_code >= 300:
        typer.echo(f"run fetch failed ({r.status_code}): {r.text}")
        sys.exit(1)
    typer.echo(json.dumps(r.json(), indent=2, default=str))
