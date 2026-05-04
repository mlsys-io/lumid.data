"""``lumid-data`` CLI entrypoint (typer)."""

import typer

from . import commands

app = typer.Typer(
    name="lumid-data",
    help="Data management service control surface (CRUD + agent + MCP).",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(commands.stack.app, name="stack", help="Local docker-compose lifecycle.")
app.add_typer(commands.sql.app, name="sql", help="Run SQL via /sql/v1.")
app.add_typer(commands.storage.app, name="storage", help="Object CRUD via /storage/v1.")
app.add_typer(commands.agent.app, name="agent", help="Run goals via /agent/v1.")
app.add_typer(commands.admin.app, name="admin", help="Audit + agent-run views.")
app.command("healthz", help="Probe the data-plane /healthz endpoint.")(
    commands.healthz.run
)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
