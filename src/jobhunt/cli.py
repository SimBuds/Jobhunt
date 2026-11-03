"""Typer entry point for the visible CLI. db/config are hidden internals."""

from __future__ import annotations

import os
import sys

import click
import typer
import typer.core

from jobhunt import privacy
from jobhunt.commands import (
    add_cmd,
    analyze_cmd,
    answer_cmd,
    apply_cmd,
    config_cmd,
    convert_resume_cmd,
    db_cmd,
    discover_cmd,
    interview_prep_cmd,
    list_cmd,
    resume_cmd,
    scan_cmd,
    setup_cmd,
    track_cmd,
)
from jobhunt.config import load_config
from jobhunt.errors import JobHuntError, PrivacyError


class _ArgvRecordingGroup(typer.core.TyperGroup):
    """Keep the raw argv where the callback can read it. Click clears the
    subcommand's arguments before the group callback runs, so the callback
    alone cannot tell `config show` from `config seed` or see `--help`."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        ctx.meta["jobhunt_argv"] = list(args)
        return super().parse_args(ctx, args)


app = typer.Typer(
    help="Local-first CLI for a personal job search.",
    no_args_is_help=True,
    cls=_ArgvRecordingGroup,
)

app.command("setup", help=setup_cmd.app.info.help)(setup_cmd.run)
app.command("convert-resume", help=convert_resume_cmd.app.info.help)(convert_resume_cmd.run)
app.command("scan", help=scan_cmd.app.info.help)(scan_cmd.run)
app.command("apply", help=apply_cmd.app.info.help)(apply_cmd.run)
app.command("add", help=add_cmd.app.info.help)(add_cmd.run)
app.command("answer", help=answer_cmd.app.info.help)(answer_cmd.run)
app.command("interview-prep", help=interview_prep_cmd.app.info.help)(interview_prep_cmd.run)
app.command("resume", help=resume_cmd.app.info.help)(resume_cmd.run)
app.command("list", help=list_cmd.app.info.help)(list_cmd.run)
app.add_typer(analyze_cmd.app, name="analyze")
app.add_typer(track_cmd.app, name="track")
app.add_typer(discover_cmd.app, name="discover")
# Visible, not hidden: README routes first-run users through `db init` /
# `config show` / `config seed`, and documents `db reset` as the way to start
# over. A recovery command that never appears in `--help` cannot be found at
# the moment it is needed.
app.add_typer(db_cmd.app, name="db")
app.add_typer(config_cmd.app, name="config")


@app.callback()
def main(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="Show full tracebacks on error."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    # Everything jobhunt writes is personal: the database, the profile,
    # tailored documents, the browser profile. umask 077 makes every new file
    # 0600 and every new directory 0700, including the ones SQLite and
    # Chromium create themselves, which a per-file chmod would miss. Existing
    # files keep their mode: tightening those is the migration's job.
    os.umask(0o077)
    ctx.obj = {"debug": debug, "verbose": verbose}
    if not privacy.is_exempt(ctx.meta.get("jobhunt_argv", [])):
        try:
            privacy.check_private_paths(load_config(), privacy.repo_root())
        except PrivacyError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from e


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
