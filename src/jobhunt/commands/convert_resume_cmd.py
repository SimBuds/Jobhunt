"""`jobhunt convert-resume` — parse Resume.docx into kb/profile/*.md + verified.json."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
import typer

from jobhunt.config import config_path, load_config
from jobhunt.resume.parse_docx import VerifiedFacts, parse_baseline, write_kb_markdown, write_verified_json

app = typer.Typer(
    help="Parse Resume.docx into the KB.",
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


@app.callback(invoke_without_command=True)
def run(
    docx: Path = typer.Option(
        Path("Resume.docx"),
        "--docx",
        help="Path to the baseline resume .docx.",
    ),
) -> None:
    cfg = load_config()
    facts = parse_baseline(docx)

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
        f"{core_count} core skills; "
        f"{len(facts.skills_familiar)} familiar."
    )

    if filled:
        typer.echo(f"\napplicant: filled {len(filled)} empty field(s) in {config_path()}: {', '.join(filled)}")

    if missing:
        typer.echo(
            f"\nERROR: [applicant] is missing required fields after parsing: {', '.join(missing)}.\n"
            f"Edit {config_path()} and set them before running `scan` or `apply` — "
            f"otherwise rendered resumes will have an empty header.",
            err=True,
        )
        sys.exit(1)
