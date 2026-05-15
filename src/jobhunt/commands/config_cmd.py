"""`jobhunt config ...` — works in phase 0."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import typer

from jobhunt.commands._config_write import write_config_atomically
from jobhunt.config import config_path, load_config
from jobhunt.errors import JobHuntError

app = typer.Typer(help="Inspect and manage configuration.", no_args_is_help=True)

# ATSes that have an ingest adapter; mirrored from add_cmd. Kept here to
# avoid an import cycle.
_SEEDABLE_ATSES = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday")


def _seed_path() -> Path:
    """The repo-shipped curated seed list. Resolved relative to the package
    so it works from a `uv run` install as well as a source checkout."""
    cfg = load_config()
    return cfg.paths.kb_dir / "seeds" / "gta-employers.toml"


def _load_seeds() -> dict[str, list[str]]:
    path = _seed_path()
    if not path.is_file():
        raise JobHuntError(
            f"seed file not found at {path}.\n"
            "the curated seed list ships with the repo; ensure kb/seeds/ exists."
        )
    with path.open("rb") as f:
        data = tomllib.load(f)
    seeds: dict[str, list[str]] = {}
    for ats in _SEEDABLE_ATSES:
        entries = data.get(ats, [])
        if not isinstance(entries, list):
            raise JobHuntError(f"seed file: [{ats}] must be a list")
        seeds[ats] = [str(e) for e in entries]
    return seeds


@app.command("seed")
def seed(
    preview: bool = typer.Option(
        False, "--preview", help="Print seeds without writing to config."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Append seeds not already in config.toml."
    ),
) -> None:
    """Import the repo-curated GTA employer seed list into config.toml.

    Run `--preview` first to see what would be added. Run `--apply` to
    write — creates a config.toml.bak snapshot first. Idempotent: re-running
    `--apply` is a no-op if the config already contains every seed."""
    if not preview and not apply:
        raise typer.BadParameter("specify --preview or --apply")
    if preview and apply:
        raise typer.BadParameter("--preview and --apply are mutually exclusive")

    seeds = _load_seeds()
    cfg = load_config()

    additions: dict[str, list[str]] = {}
    for ats in _SEEDABLE_ATSES:
        existing = set(getattr(cfg.ingest, ats))
        new = [s for s in seeds[ats] if s not in existing]
        if new:
            additions[ats] = new

    if not additions:
        typer.echo("nothing to add — config already contains every seed.")
        return

    typer.echo("seeds that would be added:" if preview else "adding seeds:")
    for ats, new in additions.items():
        typer.echo(f"  [ingest.{ats}] +{len(new)}: {', '.join(new)}")

    if preview:
        typer.echo("\n--apply to write these to config.toml")
        return

    for ats, new in additions.items():
        setattr(cfg.ingest, ats, [*getattr(cfg.ingest, ats), *new])

    write_config_atomically(cfg)
    typer.echo(f"\nupdated {config_path()}. backup: {config_path()}.bak")
    typer.echo("note: any inline comments in config.toml were not preserved on write.")


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
