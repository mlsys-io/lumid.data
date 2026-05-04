"""``lumid-data stack`` — local docker-compose lifecycle (compose-shaped).

Subcommands map straight to ``docker compose`` against the repo-root
``docker-compose.yml``. Profiles are passed through with ``--profile``.
Bare ``docker compose up -d`` from the repo root works the same.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _compose_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "docker-compose.yml"
        if candidate.exists():
            return candidate.parent
    raise typer.BadParameter("docker-compose.yml not found at the repo root.")


def _docker_compose() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise typer.BadParameter("`docker` not found on PATH")
    return docker


def _run(args: list[str]) -> None:
    cmd = [_docker_compose(), "compose", *args]
    typer.echo("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=_compose_dir(), check=False)
    if proc.returncode != 0:
        sys.exit(proc.returncode)


@app.command("up")
def up(
    profile: list[str] = typer.Option(
        [], "--profile", help="Compose profile(s) to enable."
    ),
    detach: bool = typer.Option(True, "--detach/--foreground"),
) -> None:
    args = ["up"]
    if detach:
        args.append("-d")
    for p in profile:
        args = ["--profile", p, *args]
    _run(args)


@app.command("down")
def down(volumes: bool = typer.Option(False, "--volumes/--keep-volumes")) -> None:
    args = ["down"]
    if volumes:
        args.append("-v")
    _run(args)


@app.command("restart")
def restart(service: str | None = typer.Argument(None)) -> None:
    args = ["restart"]
    if service:
        args.append(service)
    _run(args)


@app.command("ps")
def ps() -> None:
    _run(["ps"])


@app.command("logs")
def logs(
    service: str | None = typer.Argument(None),
    tail: int | None = typer.Option(None, "--tail"),
    since: str | None = typer.Option(None, "--since"),
    follow: bool = typer.Option(False, "-f", "--follow"),
) -> None:
    args = ["logs"]
    if follow:
        args.append("-f")
    if tail is not None:
        args.extend(["--tail", str(tail)])
    if since is not None:
        args.extend(["--since", since])
    if service:
        args.append(service)
    _run(args)


@app.command("build")
def build(service: str | None = typer.Argument(None)) -> None:
    args = ["build"]
    if service:
        args.append(service)
    _run(args)


@app.command("pull")
def pull(service: str | None = typer.Argument(None)) -> None:
    args = ["pull"]
    if service:
        args.append(service)
    _run(args)
