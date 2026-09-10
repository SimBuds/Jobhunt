"""Run the pipeline's audit checks against the rendered lane resumes.

Audits the output of `jobhunt resume`, which renders one base resume per
`kb/lanes/*.md` brief into `data/resumes/`. Lanes are discovered through
`resume_cmd.discover_lanes`, so adding a lane brief brings it under audit with
no change here.

`pipeline.audit.audit()` takes a TailoredResume plus a CoverLetter straight
from the apply loop. The lane resumes are standalone documents with no cover,
so this parses each rendered .docx back into a TailoredResume and runs every
applicable check at the pipeline's own thresholds.

Parsing the .docx rather than re-running `build_lane_resumes.build()` is
deliberate: it audits what the file actually says, so a renderer bug or a
hand-edit in Word is caught, not just a mistake in the builder's composition.

The two cover-dependent checks — the cover validator and the resume/cover
alignment scan — are reported as not applicable rather than run against a stub.
A fabricated cover produces violations and an alignment flag that mean nothing
here, and would drag both verdicts to `revise` for no reason.

Keyword coverage is scored against each lane's own `kb/lanes/` profile, which
is what those files describe. Read 100% as "the lane profile is satisfied", not
"any posting in this lane is satisfied" — a real JD will name things the
profile does not.

Run:   uv run python scripts/audit_lane_resumes.py
Exits: 0 ship, 1 revise, 2 block (worst verdict across both lanes), so this
       drops into a pre-commit hook or CI step as-is.
"""

from __future__ import annotations

import json
from pathlib import Path

from docx import Document

from jobhunt.commands.resume_cmd import discover_lanes
from jobhunt.config import load_config
from jobhunt.errors import PipelineError
from jobhunt.pipeline._specificity import MIN_METRIC_RETENTION_PCT, specificity_report
from jobhunt.pipeline._untrusted import hidden_text_flags, scrub_jd
from jobhunt.pipeline.audit import (
    HARD_COVERAGE_FLOOR_PCT,
    MIN_KEYWORD_COVERAGE_PCT,
    _extract_must_haves_from_jd,
    keyword_coverage,
)
from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredProject,
    TailoredResume,
    TailoredRole,
    _enforce_no_fabrication,
)

REPO = Path(__file__).resolve().parent.parent
HEADINGS = frozenset(
    {
        "SUMMARY",
        "TECHNICAL SKILLS",
        "PROFESSIONAL EXPERIENCE",
        "PROJECTS",
        "CERTIFICATIONS & EDUCATION",
    }
)
_VERDICT_RANK = {"ship": 0, "revise": 1, "block": 2}


def _split_skills(items: str) -> list[str]:
    """Paren-aware comma split, mirroring `parse_docx._split_skills` so a value
    like "Shopify (Liquid, Custom Themes)" stays one item."""
    parts: list[str] = []
    depth = 0
    buf = ""
    for ch in items:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def parse_rendered(path: Path) -> TailoredResume:
    """Rebuild a TailoredResume from what the rendered document actually says.

    Keyed to `render_docx.render`'s structure: bold "Title | Employer" plus a
    tab-separated date, bullets carrying the "List Bullet" style, and a
    "Stack: " row under each project name.
    """
    section: str | None = None
    summary = ""
    cats: list[TailoredCategory] = []
    roles: list[TailoredRole] = []
    projects: list[TailoredProject] = []
    certs: list[str] = []
    education: list[str] = []

    for para in Document(str(path)).paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if text in HEADINGS:
            section = text
            continue
        is_bullet = para.style.name == "List Bullet"

        if section == "SUMMARY":
            summary = text
        elif section == "TECHNICAL SKILLS" and ":" in text:
            label, items = text.split(":", 1)
            cats.append(TailoredCategory(label.strip(), _split_skills(items)))
        elif section == "PROFESSIONAL EXPERIENCE":
            if is_bullet and roles:
                roles[-1].bullets.append(text)
            elif not is_bullet:
                head, _, dates = text.partition("\t")
                title, _, employer = head.partition(" | ")
                roles.append(TailoredRole(title.strip(), employer.strip(), dates.strip(), []))
        elif section == "PROJECTS":
            if is_bullet and projects:
                projects[-1].bullets.append(text)
            elif text.startswith("Stack:") and projects:
                projects[-1].stack.extend(_split_skills(text.split(":", 1)[1]))
            elif not is_bullet:
                name, _, url = text.partition("\t")
                projects.append(TailoredProject(name.strip(), url.strip(), [], []))
        elif section == "CERTIFICATIONS & EDUCATION":
            (certs if "Certified" in text else education).append(text)

    return TailoredResume(
        summary=summary,
        skills_categories=cats,
        roles=roles,
        certifications=certs,
        education=education,
        coursework=[],
        model="audit",
        projects=projects,
    )


def _lane_title(jd: str) -> str | None:
    for line in jd.splitlines():
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip()
    return None


def audit_lane(path: Path, lane_path: Path, verified: dict) -> str:
    tailored = parse_rendered(path)
    jd = lane_path.read_text(encoding="utf-8")

    label = f"{path.name}   vs   {lane_path.relative_to(REPO)}"
    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")
    print(
        f"parsed: {len(tailored.skills_categories)} skill rows, "
        f"{len(tailored.roles)} roles, "
        f"{sum(len(r.bullets) for r in tailored.roles)} work points, "
        f"{len(tailored.projects)} project(s)"
    )

    fabrication: list[str] = []
    try:
        _enforce_no_fabrication(tailored, verified)
    except PipelineError as exc:
        fabrication.append(str(exc))

    doc_text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    injection = hidden_text_flags(doc_text)
    spec = specificity_report(tailored, verified)
    must_haves = _extract_must_haves_from_jd(jd, verified, _lane_title(jd))
    pct, _matched, missing = keyword_coverage(must_haves, tailored)

    print(f"\n  [1] fabrication        : {'FAIL' if fabrication else 'pass'}")
    for flag in fabrication:
        print(f"        {flag}")
    print(f"  [2] hidden text        : {'FAIL' if injection else 'pass'}")
    for flag in injection:
        print(f"        {flag}")
    print(
        f"  [3] specificity        : {spec.retained}/{spec.available} figures kept "
        f"({spec.pct}%) — threshold {MIN_METRIC_RETENTION_PCT}% "
        f"{'FAIL' if spec.flags else 'pass'}"
    )
    for flag in spec.flags:
        print(f"        {flag}")
    print(
        f"  [4] keyword coverage   : {pct}% of {len(must_haves)} lane must-haves "
        f"— ship {MIN_KEYWORD_COVERAGE_PCT}%, block {HARD_COVERAGE_FLOOR_PCT}%"
    )
    if missing:
        print(f"        missing: {', '.join(missing)}")
    print("  [-] cover validator    : n/a (no cover letter)")
    print("  [-] resume/cover align : n/a (no cover letter)")

    if fabrication or injection or (pct is not None and pct < HARD_COVERAGE_FLOOR_PCT):
        verdict = "block"
    elif spec.flags or (pct is not None and pct < MIN_KEYWORD_COVERAGE_PCT):
        verdict = "revise"
    else:
        verdict = "ship"
    print(f"\n  VERDICT: {verdict}")
    return verdict


def _resume_path(out_dir: Path, name: str, label: str) -> Path:
    """Mirror `resume_cmd`'s naming: `<Name_Slug>_Resume_<Label>.docx`."""
    prefix = f"{'_'.join(name.split())}_" if name else ""
    return out_dir / f"{prefix}Resume_{label}.docx"


def main() -> int:
    cfg = load_config()
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    verified = json.loads(verified_path.read_text(encoding="utf-8"))
    out_dir = cfg.paths.data_dir / "resumes"
    lanes = discover_lanes(cfg.paths.kb_dir)
    if not lanes:
        print(f"no lane briefs in {cfg.paths.kb_dir / 'lanes'}")
        return 0

    worst = 0
    missing: list[str] = []
    for lane in lanes.values():
        path = _resume_path(out_dir, str(verified.get("name", "")), lane.label)
        if not path.is_file():
            missing.append(f"{path.name} (run `jobhunt resume`)")
            continue
        lane_path = cfg.paths.kb_dir / "lanes" / f"{lane.slug}.md"
        worst = max(worst, _VERDICT_RANK[audit_lane(path, lane_path, verified)])

    for note in missing:
        print(f"\nnot audited: {note}")

    # The lane briefs are local files, but run them through the inbound scrub
    # as a control: it should stay silent on text that is not a fetched posting.
    for lane in lanes.values():
        brief = cfg.paths.kb_dir / "lanes" / f"{lane.slug}.md"
        flags = scrub_jd(brief.read_text(encoding="utf-8")).flags
        if flags:
            print(f"\nnote: {brief.name} tripped the inbound scrub: {flags}")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
