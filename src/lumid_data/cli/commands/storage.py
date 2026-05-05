"""``lumid-data storage`` — basic object CRUD from the terminal."""

import os
import sys
from pathlib import Path

import typer

from ...sdk import Client, ClientError

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _client() -> Client:
    return Client(
        base_url=os.environ.get("LUMID_DATA_URL", "http://127.0.0.1:9100"),
        token=os.environ.get("LUMID_TOKEN"),
    )


@app.command("get")
def get(bucket: str, path: str, out: Path = typer.Option(Path("-"))) -> None:
    try:
        data = _client().storage_get(bucket, path)
    except ClientError as exc:
        typer.echo(f"get failed: {exc}")
        sys.exit(1)
    if str(out) == "-":
        sys.stdout.buffer.write(data)
    else:
        out.write_bytes(data)
        typer.echo(f"wrote {len(data)} bytes to {out}")


@app.command("put")
def put(
    bucket: str,
    path: str,
    src: Path = typer.Argument(..., help="local file"),
    mime: str | None = typer.Option(None, "--mime"),
) -> None:
    try:
        result = _client().storage_put(bucket, path, src.read_bytes(), mime=mime)
    except ClientError as exc:
        typer.echo(f"put failed: {exc}")
        sys.exit(1)
    typer.echo(result)


@app.command("ls")
def ls(bucket: str, prefix: str | None = None, limit: int = 100) -> None:
    try:
        items = _client().storage_list(bucket, prefix=prefix, limit=limit)
    except ClientError as exc:
        typer.echo(f"ls failed: {exc}")
        sys.exit(1)
    for item in items:
        typer.echo(f"{item['key']}\t{item['size']}\t{item.get('etag', '')}")


@app.command("rm")
def rm(bucket: str, path: str) -> None:
    try:
        _client().storage_delete(bucket, path)
    except ClientError as exc:
        typer.echo(f"rm failed: {exc}")
        sys.exit(1)
    typer.echo(f"deleted {bucket}/{path}")
