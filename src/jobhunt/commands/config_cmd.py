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


@app.command("calibrate")
def calibrate() -> None:
    """Show interview-rate per score band to help tune --min-score.

    Reads all applications from the DB and groups them by the score the job
    received at scoring time. An application counts as an 'interview' if its
    status is 'interviewing', 'offer', or 'rejected' (i.e. it got far enough
    to generate a response). Designed to be run after ~20+ applications so the
    sample size is useful.
    """
    from jobhunt.db import connect

    cfg = load_config()
    conn = connect(cfg.paths.db_path)
    try:
        rows = list(
            conn.execute(
                """
                SELECT
                    s.score,
                    a.status
                FROM applications a
                JOIN scores s ON s.job_id = a.job_id
                WHERE a.status NOT IN ('drafted', 'withdrawn')
                ORDER BY s.score
                """
            )
        )
    finally:
        conn.close()

    if not rows:
        typer.echo("No applications with scores yet. Apply to some jobs first.")
        return

    BANDS = [(85, 101, "85–100"), (75, 85, "75–84"), (65, 75, "65–74"), (0, 65, "< 65")]
    INTERVIEW_STATUSES = {"interviewing", "offer", "rejected"}

    typer.echo(f"\n{'Band':<12} {'Applied':>8} {'Interviews':>11} {'Rate':>7}")
    typer.echo("-" * 42)
    for lo, hi, label in BANDS:
        band_rows = [r for r in rows if lo <= r["score"] < hi]
        applied = len(band_rows)
        interviews = sum(1 for r in band_rows if r["status"] in INTERVIEW_STATUSES)
        rate = f"{100 * interviews / applied:.0f}%" if applied else "—"
        typer.echo(f"{label:<12} {applied:>8} {interviews:>11} {rate:>7}")

    total = len(rows)
    total_interviews = sum(1 for r in rows if r["status"] in INTERVIEW_STATUSES)
    typer.echo("-" * 42)
    typer.echo(
        f"{'TOTAL':<12} {total:>8} {total_interviews:>11} "
        f"{100 * total_interviews / total:.0f}%"
        if total
        else ""
    )
    typer.echo(
        "\nCurrent min-score: "
        + str(cfg.pipeline.min_score)
        + "  (set pipeline.min_score in config.toml to change)"
    )
