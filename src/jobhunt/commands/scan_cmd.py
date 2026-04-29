"""`job-seeker scan` — pull GTA jobs and score them. Implemented in P2."""

from __future__ import annotations

import typer

from jobhunt.commands._stub import stub

app = typer.Typer(help="Ingest GTA-scoped jobs and score them.", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def run() -> None:
    stub(2, "scan")
