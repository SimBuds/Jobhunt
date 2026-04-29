"""Helpers for phase-N stubs."""

from __future__ import annotations

import typer


def stub(phase: int, command: str) -> None:
    typer.echo(f"`{command}` is not implemented yet (phase {phase}).", err=True)
    raise typer.Exit(code=2)
