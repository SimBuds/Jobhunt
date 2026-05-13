from __future__ import annotations

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
from jobhunt.config import Config


def ensure_profile(cfg: Config) -> None:
    """Bail with a friendly message if `convert-resume` hasn't been run yet.

    `kb/profile/verified.json` is the source-of-truth that scoring, tailoring,
    and listing all depend on. Running scan/list/apply without it produces
    confusing downstream errors, so guard at the command entry point.
    """
    verified = cfg.paths.kb_dir / "profile" / "verified.json"
    if not verified.is_file():
        typer.echo(
            f"error: missing {verified}\n"
            "run `jobhunt convert-resume` first to parse your baseline resume.",
            err=True,
        )
        raise typer.Exit(code=1)


__all__ = [
    "analyze_cmd",
    "apply_cmd",
    "config_cmd",
    "convert_resume_cmd",
    "db_cmd",
    "discover_cmd",
    "ensure_profile",
    "list_cmd",
    "scan_cmd",
]
