"""`jobhunt convert-resume`.

Parse the baseline resume into kb/profile/*.md + verified.json. The file is
located by `resume.locate` (any root-level .docx/.pdf named *resume*), not by
one hard-coded filename.
"""

from __future__ import annotations

import json
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

# The `https?://` scheme is OPTIONAL on every URL pattern below. Resumes
# overwhelmingly print bare domains — `linkedin.com/in/name`, `github.com/user`
# — because a printed page has no link to click, so requiring the scheme meant
# `linkedin_url`/`github_url` were never extracted from a conventionally
# formatted resume. Since both are in `_REQUIRED_FIELDS`, that failure exited
# `convert-resume` 1 for essentially every new user. Matches are normalised
# back to an absolute URL by `_normalize_url`.
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/\S+", re.IGNORECASE)
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/\S+", re.IGNORECASE)

# Portfolio: a scheme-ful URL, or a bare domain on a known TLD. The TLD
# allowlist is what keeps a bare-domain pattern from eating library names that
# happen to look like hosts (`Node.js`, `Next.js`) out of a title line.
_PORTFOLIO_TLDS = (
    "com", "net", "org", "io", "dev", "ca", "co", "me", "ai", "app", "sh",
    "xyz", "tech", "site", "page", "blog", "design", "studio", "us", "uk",
)
_PORTFOLIO_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*"
    r"\.(?:" + "|".join(_PORTFOLIO_TLDS) + r")"
    r"(?:/\S*)?",
    re.IGNORECASE,
)
_REGION_EXPANSIONS = {
    "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia", "AB": "Alberta",
    "MB": "Manitoba", "SK": "Saskatchewan", "NS": "Nova Scotia", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "PE": "Prince Edward Island",
    "YT": "Yukon", "NT": "Northwest Territories", "NU": "Nunavut",
}

# Both spellings a resume might use, mapped to the canonical long form.
_REGION_BY_TOKEN: dict[str, str] = {
    **{code.upper(): full for code, full in _REGION_EXPANSIONS.items()},
    **{full.upper(): full for full in _REGION_EXPANSIONS.values()},
}

# "City, REGION" anywhere in the line, anchored on the *region* rather than on
# the start of the string. The previous pattern was `^`-anchored, so it found
# nothing whenever the contact line opened with anything other than the city —
# a job title, most commonly — and city/region silently kept their Toronto /
# Ontario defaults for every such resume.
#
# Anchoring on a known region token is what makes a free-floating search safe:
# an unconstrained `([A-Za-z .'-]+?),\s*([A-Za-z]{2,})` matches the leftmost
# comma in the line, which on a title-first contact line yields nonsense like
# city="AI Automation". The city is then the ≤3-word run immediately before the
# comma; joins are a single space, so a double space or a `|` ends the run
# naturally, which is how resumes separate these fields.
_CITY_REGION_RE = re.compile(
    r"([A-Za-z][A-Za-z.'-]*(?:[ ][A-Za-z.'-]+){0,2})\s*,\s*("
    + "|".join(re.escape(t) for t in sorted(_REGION_BY_TOKEN, key=len, reverse=True))
    + r")(?![A-Za-z])",
    re.IGNORECASE,
)

_REQUIRED_FIELDS = ("full_name", "email", "linkedin_url", "github_url")

# Kept in sync with `IngestConfig.user_agent` in config.py. Matched as a
# substring so only the contact address is replaced, leaving any product/
# version prefix the user may have set alone.
_PLACEHOLDER_CONTACT = "your-email@example.com"
_DEFAULT_USER_AGENT = f"jobhunt/0.1 (+personal-use; {_PLACEHOLDER_CONTACT})"


def _normalize_url(raw: str) -> str:
    """Trim trailing punctuation and add the scheme a printed resume omits."""
    url = raw.strip().rstrip("/.,;)")
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _parse_contact_line(contact: str) -> dict[str, str]:
    """Extract identity fields from a resume contact line. Returns only fields found."""
    found: dict[str, str] = {}

    if m := _EMAIL_RE.search(contact):
        found["email"] = m.group(0).removeprefix("mailto:")

    if m := _LINKEDIN_RE.search(contact):
        found["linkedin_url"] = _normalize_url(m.group(0))
    if m := _GITHUB_RE.search(contact):
        found["github_url"] = _normalize_url(m.group(0))

    # Portfolio: first URL that isn't linkedin/github. Searched with the email
    # removed first — a bare-domain pattern would otherwise match the domain
    # inside `name@outlook.com` and record the mail host as a portfolio.
    without_email = _EMAIL_RE.sub(" ", contact)
    for m in _PORTFOLIO_RE.finditer(without_email):
        url = _normalize_url(m.group(0))
        if "linkedin.com" in url.lower() or "github.com" in url.lower():
            continue
        found["portfolio_url"] = url
        break

    # Phone — search the contact line with URLs/email stripped to avoid matching digits in them.
    stripped = _PORTFOLIO_RE.sub(" ", without_email)
    if m := _PHONE_RE.search(stripped):
        found["phone"] = re.sub(r"\s+", " ", m.group(0)).strip()

    if m := _CITY_REGION_RE.search(contact):
        found["city"] = m.group(1).strip()
        found["region"] = _REGION_BY_TOKEN[m.group(2).strip().upper()]

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

    # The default `ingest.user_agent` ships a placeholder contact address, and
    # nothing in `setup` ever prompts for it — so every request to a public ATS
    # API went out identifying the operator as `your-email@example.com`. The
    # real address is right here in the parsed contact line. Swapped only while
    # the placeholder is still in place, so a hand-edited UA is never touched.
    if email := parsed.get("email"):
        ingest = data.setdefault("ingest", {})
        user_agent = str(ingest.get("user_agent", _DEFAULT_USER_AGENT))
        if _PLACEHOLDER_CONTACT in user_agent:
            ingest["user_agent"] = user_agent.replace(_PLACEHOLDER_CONTACT, email)
            filled.append("ingest.user_agent")

    if filled:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(tomli_w.dumps(data))

    still_missing = [k for k in _REQUIRED_FIELDS if not applicant.get(k)]
    return filled, still_missing


# Warnings where the parser KEPT the content and only noted a fallback. Every
# other warning is treated as data loss.
#
# The list is deliberately the *benign* side rather than the lossy side. An
# allow-list of lossy phrasings ("dropped", "skipped") silently stops working
# the moment a new warning describes loss in different words — which is exactly
# the failure class this guard exists to prevent, and exactly what happened to
# the skill-label allow-list it replaced. Adding a warning is now safe by
# default: an unclassified one blocks the write and gets noticed.
_BENIGN_WARNING_MARKERS: tuple[str, ...] = (
    # Cert-vs-education classifier fell back; the entry is still recorded.
    "defaulted to education",
    # Unrecognised skill label: items are kept in Core, just not bucketed
    # precisely. Advisory by construction (see parse_docx `_infer_skill_bucket`).
    "assigned to Core",
)


def _dropped_content_warnings(warnings: list[str]) -> list[str]:
    """Warnings that mean resume content was *discarded*, not merely noted.

    Fails closed: anything not explicitly listed in `_BENIGN_WARNING_MARKERS`
    counts as loss. A partial profile is worse than no profile — scoring and
    tailoring treat `verified.json` as the whole truth — so the safe default
    for an unfamiliar warning is to refuse the write and let a human look.
    """
    return [
        w for w in warnings
        if not any(marker in w for marker in _BENIGN_WARNING_MARKERS)
    ]


# Buckets worth comparing against the previous snapshot, paired with the label
# used in the warning text.
#
# `_dropped_content_warnings` above can only classify warnings the parser
# actually emitted, and `parse_baseline` only warns about rows it SAW and could
# not place. A row that is simply *gone* from the document produces no warning
# at all, so the guard never fires. That blind spot is not hypothetical: the
# 2026-07-25 reformat dropped the "Familiar" row, `skills_familiar` silently
# became `[]`, and every consumer then read the empty bucket as fact. The tailor
# rejected every Familiar skill as unverified (breaking `apply` outright), and
# the score clamp's Familiar-only cap stopped firing without a word.
_TRACKED_BUCKETS: tuple[tuple[str, str], ...] = (
    ("skills_core", "Core"),
    ("skills_cms", "CMS & E-commerce"),
    ("skills_data_devops", "Data & DevOps"),
    ("skills_ai", "AI & Tooling"),
    ("skills_projects", "Project Stack"),
    ("skills_familiar", "Familiar"),
    ("work_history", "work history"),
    ("projects", "projects"),
)

# Buckets whose disappearance is a legitimate authoring choice rather than a
# parse failure, so losing one advises instead of blocking.
#
# Familiar is the only such bucket. A "Familiar"/"Exposure" row is optional on a
# resume in a way no other tracked bucket is: dropping it narrows what the
# candidate CLAIMS, and every downstream consumer already treats the empty
# bucket as "no Familiar skills" rather than "unknown" —
# `tailor._complete_familiar_bucket` early-returns, `tailor.md` rule 10 omits
# the category rather than inventing items, and `score._all_matched_are_familiar`
# returns False so the Familiar-only cap simply never fires. Tailoring then
# draws on the main buckets, which is the correct behaviour for a resume that
# claims no side skills.
#
# The other buckets stay blocking. An absent Core/Data/AI row is not a narrower
# claim, it is a partial profile: the fabrication guard rejects every skill
# missing from `verified.json`, so a silently-emptied Data & DevOps bucket
# permanently strips Docker/AWS/Postgres from every future tailored document.
_OPTIONAL_BUCKETS: frozenset[str] = frozenset({"skills_familiar"})


def _bucket_regressions(
    facts: VerifiedFacts, verified_path: Path
) -> tuple[list[str], list[str]]:
    """Buckets that carried content last run and parse empty now.

    Returns `(blocking, advisory)`. Blocking entries are appended to the parser
    warnings so they flow through `_dropped_content_warnings` and inherit the
    existing fail-closed write refusal, its `--force` escape hatch, and its exit
    code; those strings deliberately avoid every `_BENIGN_WARNING_MARKERS`
    substring so they classify as loss. Advisory entries are for
    `_OPTIONAL_BUCKETS` — reported to the user but never counted as loss, so the
    write proceeds.

    Advisory is returned as a separate list rather than smuggled past
    `_dropped_content_warnings` with a benign marker: classification by
    substring is what the marker list is for, and an optional bucket is a
    property of the bucket, not of the wording of its message.

    Both lists are `[]` when there is no readable prior snapshot. A first run has
    nothing to regress against, and a corrupt or hand-edited snapshot is not
    evidence the *resume* lost anything, so neither may block the write.
    """
    if not verified_path.is_file():
        return [], []
    try:
        previous = json.loads(verified_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    if not isinstance(previous, dict):
        return [], []

    blocking: list[str] = []
    advisory: list[str] = []
    for key, label in _TRACKED_BUCKETS:
        was = previous.get(key) or []
        now = getattr(facts, key, None) or []
        if not (was and not now):
            continue
        if key in _OPTIONAL_BUCKETS:
            advisory.append(
                f"{label}: had {len(was)} item(s) in the previous "
                f"verified.json and parses to nothing now. This bucket is "
                f"optional — tailoring will draw on the main skill buckets and "
                f"omit the {label} section. Re-add the row if that is wrong."
            )
        else:
            blocking.append(
                f"{label}: had {len(was)} item(s) in the previous "
                f"verified.json and parses to nothing now. The matching row is "
                f"likely missing from the resume after an edit."
            )
    return blocking, advisory


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

    verified = cfg.paths.kb_dir / "profile" / "verified.json"
    # Appended BEFORE classification so a vanished row inherits the same
    # fail-closed refusal the parser's own loss warnings already get. Optional
    # buckets come back separately and never gate the write.
    bucket_blocking, bucket_advisory = _bucket_regressions(facts, verified)
    warnings = warnings + bucket_blocking

    for note in bucket_advisory:
        typer.echo(f"note: {note}", err=True)

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
