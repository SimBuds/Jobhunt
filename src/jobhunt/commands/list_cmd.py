"""`job-seeker list` — pipeline view with weekly tracking. Implemented in P5."""

from __future__ import annotations

import typer

from jobhunt.commands._stub import stub

app = typer.Typer(
    help="List scored jobs and weekly application pipeline.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def run(
    week: int = typer.Option(0, "--week", help="0 = current week, 1 = last week, ..."),
    status: str | None = typer.Option(None, "--status"),
    min_score: int | None = typer.Option(None, "--min-score"),
    source: str | None = typer.Option(None, "--source"),
) -> None:
    _ = (week, status, min_score, source)
    stub(5, "list")
