"""`job-seeker convert-resume` — parse Baseline.docx into kb/profile/*.md + verified.json."""

from __future__ import annotations

from pathlib import Path

import typer

from jobhunt.config import load_config
from jobhunt.resume.parse_docx import parse_baseline, write_kb_markdown, write_verified_json

app = typer.Typer(
    help="Parse Casey_Hsu_Resume_Baseline.docx into the KB.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def run(
    docx: Path = typer.Option(
        Path("Casey_Hsu_Resume_Baseline.docx"),
        "--docx",
        help="Path to the baseline resume .docx.",
    ),
) -> None:
    cfg = load_config()
    facts = parse_baseline(docx)

    verified = cfg.paths.kb_dir / "profile" / "verified.json"
    write_verified_json(facts, verified)
    written = write_kb_markdown(facts, cfg.paths.kb_dir)

    typer.echo(f"verified facts: {verified}")
    for p in written:
        typer.echo(f"regenerated:    {p}")
    core_count = (
        len(facts.skills_core)
        + len(facts.skills_cms)
        + len(facts.skills_data_devops)
        + len(facts.skills_ai)
    )
    typer.echo(
        f"\n{len(facts.work_history)} role(s); "
        f"{core_count} core skills; "
        f"{len(facts.skills_familiar)} familiar."
    )
