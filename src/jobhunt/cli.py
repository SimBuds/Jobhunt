"""Typer entry point. Four user-facing commands; db/config are hidden internals."""

from __future__ import annotations

import sys

import typer

from jobhunt.commands import (
    apply_cmd,
    config_cmd,
    convert_resume_cmd,
    db_cmd,
    list_cmd,
    scan_cmd,
)
from jobhunt.errors import JobHuntError

app = typer.Typer(
    help="Local-first CLI for Casey's Toronto-area job hunt.",
    no_args_is_help=True,
)

app.add_typer(convert_resume_cmd.app, name="convert-resume")
app.add_typer(scan_cmd.app, name="scan")
app.add_typer(apply_cmd.app, name="apply")
app.add_typer(list_cmd.app, name="list")
app.add_typer(db_cmd.app, name="db", hidden=True)
app.add_typer(config_cmd.app, name="config", hidden=True)


@app.callback()
def main(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="Show full tracebacks on error."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    ctx.obj = {"debug": debug, "verbose": verbose}


def _run() -> None:
    try:
        app()
    except JobHuntError as e:
        if "--debug" in sys.argv:
            raise
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from e


if __name__ == "__main__":
    _run()
