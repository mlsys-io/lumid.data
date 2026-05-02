"""``lumid-data`` CLI entrypoint (typer)."""

import typer

from . import commands

app = typer.Typer(
    name="lumid-data",
    help="Data plane control surface for lumid + FlowMesh + Lumilake.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(commands.stack.app, name="stack", help="Local docker-compose lifecycle.")
app.add_typer(commands.source.app, name="source", help="Source registry CRUD.")
app.add_typer(commands.dlq.app, name="dlq", help="Dead-letter queue inspection.")
app.command("healthz", help="Probe the data-plane /healthz endpoint.")(commands.healthz.run)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
