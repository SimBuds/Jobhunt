"""`jobhunt convert-resume`.

Parse the baseline resume into kb/profile/*.md + verified.json. The file is
located by `resume.locate` (any root-level .docx/.pdf named *resume*), not by
one hard-coded filename.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
import typer

from jobhunt.config import config_path, load_config
from jobhunt.errors import PipelineError
from jobhunt.resume.locate import describe_choice, find_baseline_resume
from jobhunt.resume.parse_docx import (
    VerifiedFacts,
    parse_baseline,
    write_kb_markdown,
    write_verified_json,
)

app = typer.Typer(
    help="Parse Baseline_Resume.docx into the KB.",
    invoke_without_command=True,
)


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/\S+", re.IGNORECASE)
_GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/\S+", re.IGNORECASE)
_PORTFOLIO_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_CITY_REGION_RE = re.compile(r"^\s*([A-Za-z][A-Za-z .'-]+?)\s*,\s*([A-Za-z]{2,})")

_REGION_EXPANSIONS = {
    "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia", "AB": "Alberta",
    "MB": "Manitoba", "SK": "Saskatchewan", "NS": "Nova Scotia", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "PE": "Prince Edward Island",
    "YT": "Yukon", "NT": "Northwest Territories", "NU": "Nunavut",
}

_REQUIRED_FIELDS = ("full_name", "email", "linkedin_url", "github_url")


def _parse_contact_line(contact: str) -> dict[str, str]:
    """Extract identity fields from a resume contact line. Returns only fields found."""
    found: dict[str, str] = {}

    if m := _EMAIL_RE.search(contact):
        found["email"] = m.group(0).removeprefix("mailto:")

    if m := _LINKEDIN_RE.search(contact):
        found["linkedin_url"] = m.group(0).rstrip("/.,;)")
    if m := _GITHUB_RE.search(contact):
        found["github_url"] = m.group(0).rstrip("/.,;)")

    # Portfolio: first http(s) URL that isn't linkedin/github.
    for m in _PORTFOLIO_RE.finditer(contact):
        url = m.group(0).rstrip("/.,;)")
        if "linkedin.com" in url or "github.com" in url:
            continue
        found["portfolio_url"] = url
        break

    # Phone — search the contact line with URLs/email stripped to avoid matching digits in them.
    stripped = _PORTFOLIO_RE.sub(" ", contact)
    stripped = _EMAIL_RE.sub(" ", stripped)
    if m := _PHONE_RE.search(stripped):
        found["phone"] = re.sub(r"\s+", " ", m.group(0)).strip()

    if m := _CITY_REGION_RE.match(contact):
        found["city"] = m.group(1).strip()
        region = m.group(2).strip()
        found["region"] = _REGION_EXPANSIONS.get(region.upper(), region)

    return found


def _sync_applicant(facts: VerifiedFacts) -> tuple[list[str], list[str]]:
    """Backfill empty `[applicant]` fields in config.toml from parsed resume facts.

    Returns (filled, still_missing) where each is a list of field names.
    """
    cfg_path = config_path()
    data: dict[str, Any] = {}
    if cfg_path.exists():
        data = tomllib.loads(cfg_path.read_text())
    applicant = data.setdefault("applicant", {})

    parsed = _parse_contact_line(facts.contact_line)
    parsed["full_name"] = facts.name

    filled: list[str] = []
    for key, value in parsed.items():
        if not value:
            continue
        if not applicant.get(key):  # only fill empty/missing
            applicant[key] = value
            filled.append(key)

    if filled:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(tomli_w.dumps(data))

    still_missing = [k for k in _REQUIRED_FIELDS if not applicant.get(k)]
    return filled, still_missing


def _dropped_content_warnings(warnings: list[str]) -> list[str]:
    """Warnings that mean resume content was *discarded*, not merely noted.

    `parse_baseline` phrases every lossy outcome with 'dropped' or 'skipped'
    (unrecognized skill label, bullet before any role header, unparseable role
    header, non 'Label: items' line). Anything else is advisory and must not
    block a write — the guard exists to stop silent data loss, not to demand a
    perfectly clean parse.
    """
    return [w for w in warnings if "dropped" in w or "skipped" in w]


@app.callback(invoke_without_command=True)
def run(
    docx: Path | None = typer.Option(
        None,
        "--docx",
        help=(
            "Path to the baseline resume. Default: the newest root-level "
            ".docx/.pdf with 'resume' in the filename."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Write kb/profile/ even when the parser dropped skills or roles.",
    ),
) -> None:
    cfg = load_config()
    try:
        docx = find_baseline_resume(explicit=docx)
    except PipelineError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(describe_choice(docx))

    if docx.suffix.lower() == ".pdf":
        typer.echo(
            f"error: {docx.name} is a PDF; only .docx can be parsed today. "
            "Export the resume to .docx, or pass --docx <file.docx>.",
            err=True,
        )
        raise typer.Exit(code=2)

    facts, warnings = parse_baseline(docx)

    dropped = _dropped_content_warnings(warnings)
    if dropped and not force:
        typer.echo(
            f"error: parser dropped content from {docx} "
            f"({len(dropped)} of {len(warnings)} warning(s)). "
            "kb/profile/ NOT written.",
            err=True,
        )
        for w in dropped:
            typer.echo(f"  - {w}", err=True)
        typer.echo(
            "\nA partial profile is worse than none: scoring and tailoring "
            "treat kb/profile/verified.json as the whole truth, and the "
            "fabrication guard rejects any skill missing from it. Fix the "
            "resume formatting (or the parser), then re-run. "
            "Use --force only if the dropped content is genuinely unwanted.",
            err=True,
        )
        raise typer.Exit(code=1)

    verified = cfg.paths.kb_dir / "profile" / "verified.json"
    write_verified_json(facts, verified)
    written = write_kb_markdown(facts, cfg.paths.kb_dir)

    filled, missing = _sync_applicant(facts)

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
        f"{len(facts.projects)} project(s); "
        f"{core_count} core skills; "
        f"{len(facts.skills_projects)} project skills; "
        f"{len(facts.skills_familiar)} familiar."
    )

    if warnings:
        typer.echo(f"\nparse warnings ({len(warnings)}):", err=True)
        for w in warnings:
            typer.echo(f"  - {w}", err=True)

    if filled:
        typer.echo(
            f"\napplicant: filled {len(filled)} empty field(s) in "
            f"{config_path()}: {', '.join(filled)}"
        )

    if missing:
        typer.echo(
            "\nERROR: [applicant] is missing required fields after parsing: "
            f"{', '.join(missing)}.\n"
            f"Edit {config_path()} and set them before running `scan` or `apply` — "
            f"otherwise rendered resumes will have an empty header.",
            err=True,
        )
        sys.exit(1)
