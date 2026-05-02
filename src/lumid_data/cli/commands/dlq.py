"""``lumid-data dlq`` — DLQ inspection."""

import json
import os
import sys

import httpx
import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _base_url() -> str:
    return os.environ.get("LUMID_DATA_URL", "http://127.0.0.1:9100").rstrip("/")


def _headers() -> dict[str, str]:
    h: dict[str, str] = {}
    token = os.environ.get("LUMID_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


@app.command("list")
def list_(
    source_id: str | None = typer.Option(None),
    replayed: bool | None = typer.Option(None),
) -> None:
    params: dict[str, str] = {}
    if source_id:
        params["source_id"] = source_id
    if replayed is not None:
        params["replayed"] = "true" if replayed else "false"
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{_base_url()}/v1/admin/dlq", params=params, headers=_headers())
    if r.status_code >= 300:
        typer.echo(f"dlq list failed ({r.status_code}): {r.text}")
        sys.exit(1)
    typer.echo(json.dumps(r.json(), indent=2))


@app.command("mark-replayed")
def mark_replayed(dlq_id: str) -> None:
    with httpx.Client(timeout=10.0) as c:
        r = c.post(
            f"{_base_url()}/v1/admin/dlq/{dlq_id}/mark-replayed",
            headers=_headers(),
        )
    typer.echo(r.text)
