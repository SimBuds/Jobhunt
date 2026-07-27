"""`jobhunt resume` — render lane-focused base resumes.

Runs the existing tailor pipeline (fabrication checks, JD-skill backfill,
one-page shrink) against a hand-written lane brief (`kb/lanes/<slug>.md`)
instead of a real job posting, then renders the same ATS-safe DOCX the apply
flow ships. Base resumes serve the manual channels (LinkedIn Easy Apply,
Indeed, recruiters) the auto-apply pipeline never touches, and are
regenerable whenever `verified.json` changes:

    jobhunt resume                 # all three lanes
    jobhunt resume --focus ai      # one lane

Output: `data/resumes/<Name>_Resume_<Lane>.docx` plus the tailored JSON
beside it for review.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import typer

from jobhunt.config import Config, load_config
from jobhunt.errors import JobHuntError, PipelineError
from jobhunt.models import Job
from jobhunt.pipeline.tailor import tailor_resume_with_retry
from jobhunt.resume.render_docx import render

app = typer.Typer(
    help="Render lane-focused base resumes (AI automation / CMS & e-commerce).",
    invoke_without_command=True,
)


@dataclass(frozen=True)
class Lane:
    """One base-resume lane: `slug` names the kb/lanes brief file, `label`
    is the DOCX filename suffix."""

    slug: str
    label: str


LANES: dict[str, Lane] = {
    "ai": Lane(slug="ai-automation", label="AI_Automation"),
    "cms": Lane(slug="cms-ecommerce", label="CMS_Ecommerce"),
}

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class LaneBrief:
    focus: str
    slug: str
    label: str
    title: str
    company: str
    description: str


def load_lane_brief(kb_dir: Path, focus: str) -> LaneBrief:
    """Parse `kb/lanes/<slug>.md`: minimal `key: value` frontmatter (title,
    company) + markdown body used verbatim as the pseudo-JD description."""
    lane = LANES[focus]
    path = kb_dir / "lanes" / f"{lane.slug}.md"
    if not path.is_file():
        raise PipelineError(f"missing lane brief {path}")
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        raise PipelineError(f"lane brief {path} has no frontmatter block")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        if value:
            meta[key.strip().lower()] = value.strip()
    title = meta.get("title", "")
    if not title:
        raise PipelineError(f"lane brief {path} frontmatter is missing `title:`")
    body = text[m.end():].strip()
    return LaneBrief(
        focus=focus,
        slug=lane.slug,
        label=lane.label,
        title=title,
        company=meta.get("company", f"Base Resume — {lane.label}"),
        description=body,
    )


def lane_job(brief: LaneBrief) -> Job:
    """Synthetic Job the tailor pipeline runs against. Never persisted."""
    return Job(
        id=f"lane-{brief.slug}",
        source="lane",
        external_id=brief.slug,
        company=brief.company,
        title=brief.title,
        location="Toronto, ON (base resume)",
        description=brief.description,
    )


def _name_and_contact(cfg: Config, verified: dict[str, object]) -> tuple[str, str]:
    """Mirror apply_cmd._render_artifacts' applicant-config header, falling
    back to verified.json when `jobhunt setup` hasn't populated applicant."""
    name = cfg.applicant.full_name or str(verified.get("name") or "")
    if cfg.applicant.email:
        contact = (
            cfg.applicant.email
            + ("  |  " + cfg.applicant.phone if cfg.applicant.phone else "")
            + f"  |  {cfg.applicant.portfolio_url}  |  {cfg.applicant.linkedin_url}  |  "
            + cfg.applicant.github_url
        )
    else:
        contact = str(verified.get("contact_line") or "")
    return name, contact


async def _render_lane(
    cfg: Config, brief: LaneBrief, verified: dict[str, object], out_dir: Path
) -> Path:
    typer.echo(f"\n=== {brief.title} ({brief.focus}) ===")
    typer.echo("    … tailoring resume (LLM, ~30–60s)")
    tailored, violations, attempts = await tailor_resume_with_retry(
        cfg, lane_job(brief), max_attempts=cfg.pipeline.tailor_retry_attempts,
    )
    if attempts > 1:
        n = len(violations)
        tag = "clean" if not n else f"{n} {'violation' if n == 1 else 'violations'} remain"
        typer.echo(f"    tailor: {attempts} attempts ({tag})")

    name, contact_line = _name_and_contact(cfg, verified)
    name_slug = "_".join(name.split()) if name else ""
    prefix = f"{name_slug}_" if name_slug else ""
    resume_path = out_dir / f"{prefix}Resume_{brief.label}.docx"
    render(tailored, contact_line=contact_line, name=name, out_path=resume_path)
    (out_dir / f"tailored-{brief.slug}.json").write_text(
        json.dumps(asdict(tailored), indent=2), encoding="utf-8"
    )
    typer.echo(f"    + {resume_path}")
    return resume_path


@app.callback(invoke_without_command=True)
def run(
    focus: str = typer.Option(
        "all",
        "--focus",
        help="Which base resume to render: ai | cms | all.",
    ),
) -> None:
    from jobhunt.commands import ensure_profile

    if focus not in ("all", *LANES):
        typer.echo(
            f"error: unknown focus {focus!r} — use one of: all, {', '.join(LANES)}",
            err=True,
        )
        raise typer.Exit(code=2)

    cfg = load_config()
    ensure_profile(cfg)

    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    verified: dict[str, object] = json.loads(verified_path.read_text(encoding="utf-8"))

    focuses = list(LANES) if focus == "all" else [focus]
    try:
        briefs = [load_lane_brief(cfg.paths.kb_dir, f) for f in focuses]
    except JobHuntError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from e

    out_dir = cfg.paths.data_dir / "resumes"
    out_dir.mkdir(parents=True, exist_ok=True)

    async def _drive() -> list[Path]:
        # Sequential on purpose: one hot local model; parallel lanes would
        # just contend for the same Ollama slot.
        return [await _render_lane(cfg, b, verified, out_dir) for b in briefs]

    try:
        paths = asyncio.run(_drive())
    except JobHuntError as e:
        typer.echo(f"  ! resume render failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"\ndone — {len(paths)} resume(s) in {out_dir}")


__all__ = ["app", "run", "LANES", "LaneBrief", "load_lane_brief", "lane_job"]
