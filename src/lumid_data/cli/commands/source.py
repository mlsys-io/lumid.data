"""``lumid-data source`` — source registry CRUD over HTTP."""

import json
import os
import sys
from pathlib import Path

import httpx
import typer
import yaml

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _base_url() -> str:
    return os.environ.get("LUMID_DATA_URL", "http://127.0.0.1:9100").rstrip("/")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    token = os.environ.get("LUMID_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


@app.command("register")
def register(
    descriptor_file: Path = typer.Argument(..., help="YAML or JSON SourceDescriptor"),
) -> None:
    text = descriptor_file.read_text()
    body = json.loads(text) if descriptor_file.suffix == ".json" else yaml.safe_load(text)
    with httpx.Client(timeout=10.0) as c:
        r = c.post(f"{_base_url()}/v1/sources", json=body, headers=_headers())
    if r.status_code >= 300:
        typer.echo(f"register failed ({r.status_code}): {r.text}")
        sys.exit(1)
    typer.echo(json.dumps(r.json(), indent=2))


@app.command("list")
def list_(
    tenant: str | None = typer.Option(None),
    app_name: str | None = typer.Option(None, "--app"),
) -> None:
    params: dict[str, str] = {}
    if tenant:
        params["tenant"] = tenant
    if app_name:
        params["app"] = app_name
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{_base_url()}/v1/sources", params=params, headers=_headers())
    if r.status_code >= 300:
        typer.echo(f"list failed ({r.status_code}): {r.text}")
        sys.exit(1)
    typer.echo(json.dumps(r.json(), indent=2))


@app.command("get")
def get(source_id: str) -> None:
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{_base_url()}/v1/sources/{source_id}", headers=_headers())
    if r.status_code >= 300:
        typer.echo(f"get failed ({r.status_code}): {r.text}")
        sys.exit(1)
    typer.echo(json.dumps(r.json(), indent=2))


@app.command("pause")
def pause(source_id: str) -> None:
    with httpx.Client(timeout=10.0) as c:
        r = c.post(f"{_base_url()}/v1/sources/{source_id}/pause", headers=_headers())
    typer.echo(r.text)


@app.command("resume")
def resume(source_id: str) -> None:
    with httpx.Client(timeout=10.0) as c:
        r = c.post(f"{_base_url()}/v1/sources/{source_id}/resume", headers=_headers())
    typer.echo(r.text)
