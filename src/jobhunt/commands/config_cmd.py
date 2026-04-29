"""`jobhunt config ...` — works in phase 0."""

from __future__ import annotations

import json

import typer

from jobhunt.config import config_path, load_config

app = typer.Typer(help="Inspect and manage configuration.", no_args_is_help=True)


@app.command("show")
def show() -> None:
    """Print the resolved configuration."""
    cfg = load_config()
    typer.echo(f"# config: {config_path()}")
    typer.echo(json.dumps(cfg.model_dump(mode="json"), indent=2, default=str))


@app.command("path")
def path() -> None:
    """Print the path to the active config file."""
    typer.echo(str(config_path()))
