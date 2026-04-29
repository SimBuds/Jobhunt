"""`job-seeker apply <id>` — tailor docs and autofill the form. Implemented in P3+P4."""

from __future__ import annotations

import typer

from jobhunt.commands._stub import stub

app = typer.Typer(
    help="Tailor resume + cover letter and autofill the application form.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def run(job_id: str = typer.Argument(..., help="Job id from `job-seeker list`.")) -> None:
    stub(3, f"apply {job_id}")
