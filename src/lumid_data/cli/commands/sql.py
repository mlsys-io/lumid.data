"""``lumid-data sql "SELECT ..."`` — psycopg passthrough from the terminal."""

import json
import os
import sys

import typer

from ...client import Client, ClientError

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Run SQL via the /sql/v1 endpoint"
)


def _client() -> Client:
    return Client(
        base_url=os.environ.get("LUMID_DATA_URL", "http://127.0.0.1:9100"),
        token=os.environ.get("LUMID_TOKEN"),
    )


@app.command()
def query(
    sql: str = typer.Argument(..., help="SQL statement"),
    params: str | None = typer.Option(None, "--params", help="JSON list of params"),
) -> None:
    parsed = json.loads(params) if params else []
    try:
        result = _client().sql(sql, parsed)
    except ClientError as exc:
        typer.echo(f"sql failed: {exc}")
        sys.exit(1)
    typer.echo(json.dumps(result, indent=2, default=str))
