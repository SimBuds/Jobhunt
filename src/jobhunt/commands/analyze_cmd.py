"""`jobhunt analyze` — aggregate analyses over scanned jobs."""

from __future__ import annotations

import typer

from jobhunt.config import load_config
from jobhunt.db import connect

app = typer.Typer(
    help="Aggregate analyses over scanned jobs.",
    no_args_is_help=True,
)


@app.command("certs", help="Show the most common certifications across scanned jobs.")
def certs(
    top: int = typer.Option(
        25,
        "--top",
        "-n",
        min=1,
        max=200,
        help="Number of top certifications to display (default 25).",
    ),
) -> None:
    from jobhunt.analyze.certs import tally
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)

    conn = connect(cfg.paths.db_path)
    try:
        rows = conn.execute(
            "SELECT title, description FROM jobs WHERE description IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        typer.echo("no jobs scanned yet — run `jobhunt scan` first.")
        raise typer.Exit(code=0)

    counts = tally(rows)
    total_jobs = len(rows)

    typer.echo(f"certification frequency across {total_jobs} scanned job(s)\n")

    if not counts:
        typer.echo("no certifications detected in job descriptions.")
        raise typer.Exit(code=0)

    top_items = counts.most_common(top)
    # Column widths.
    name_w = max(len(name) for name, _ in top_items)
    name_w = max(name_w, 12)  # min header width

    header = f"{'Certification':<{name_w}}  {'Jobs':>5}  {'%':>5}"
    typer.echo(header)
    typer.echo("-" * len(header))

    for name, count in top_items:
        pct = count / total_jobs * 100
        typer.echo(f"{name:<{name_w}}  {count:>5}  {pct:>4.1f}%")
