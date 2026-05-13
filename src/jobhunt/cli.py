"""Typer entry point. Four user-facing commands; db/config are hidden internals."""

from __future__ import annotations

import sys

import typer

from jobhunt.commands import (
    analyze_cmd,
    apply_cmd,
    config_cmd,
    convert_resume_cmd,
    db_cmd,
    discover_cmd,
    list_cmd,
    scan_cmd,
)
from jobhunt.errors import JobHuntError

app = typer.Typer(
    help="Local-first CLI for Casey's Toronto-area job hunt.",
    no_args_is_help=True,
)

app.command("convert-resume", help=convert_resume_cmd.app.info.help)(convert_resume_cmd.run)
app.command("scan", help=scan_cmd.app.info.help)(scan_cmd.run)
app.command("apply", help=apply_cmd.app.info.help)(apply_cmd.run)
app.command("list", help=list_cmd.app.info.help)(list_cmd.run)
app.add_typer(analyze_cmd.app, name="analyze")
app.add_typer(discover_cmd.app, name="discover")
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
