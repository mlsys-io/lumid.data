"""``lumid-data healthz`` — probe /healthz."""

import os

import httpx
import typer


def run() -> None:
    base = os.environ.get("LUMID_DATA_URL", "http://127.0.0.1:9100").rstrip("/")
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{base}/healthz")
        typer.echo(f"{r.status_code} {r.text}")
        if r.status_code != 200:
            raise typer.Exit(code=1)
    except httpx.HTTPError as exc:
        typer.echo(f"healthz failed: {exc}")
        raise typer.Exit(code=1)
