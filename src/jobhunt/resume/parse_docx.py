"""Parse Baseline_Resume.docx into structured `verified.json` + KB markdown.

The output of this module is the single source of truth for tailoring. Downstream
prompts must only use facts present in `verified.json` — that is the structural
enforcement of the no-fabrication rule in `kb/policies/tailoring-rules.md`
("Hard prohibitions").
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from jobhunt.errors import PipelineError

SECTION_HEADERS = {
    "SUMMARY",
    "TECHNICAL SKILLS",
    "PROFESSIONAL EXPERIENCE",
    "CERTIFICATIONS & EDUCATION",
    "PROJECTS",
}

# Alternate section headings (lowercased, whole-line) mapped onto the canonical
# headers, so a resume that does not use Casey's exact headings still sections
# correctly. The canonical names need no self-entry: `_canonical_section` checks
# an exact (case-insensitive) match first. Whole-line match keeps a body line
# like "Experience with React" from being mistaken for an "EXPERIENCE" header.
_SECTION_ALIASES: dict[str, str] = {
    "profile": "SUMMARY",
    "professional summary": "SUMMARY",
    "objective": "SUMMARY",
    "about": "SUMMARY",
    "about me": "SUMMARY",
    "skills": "TECHNICAL SKILLS",
    "tech stack": "TECHNICAL SKILLS",
    "technologies": "TECHNICAL SKILLS",
    "technical proficiencies": "TECHNICAL SKILLS",
    "experience": "PROFESSIONAL EXPERIENCE",
    "work experience": "PROFESSIONAL EXPERIENCE",
    "employment": "PROFESSIONAL EXPERIENCE",
    "employment history": "PROFESSIONAL EXPERIENCE",
    "work history": "PROFESSIONAL EXPERIENCE",
    "education": "CERTIFICATIONS & EDUCATION",
    "education & certifications": "CERTIFICATIONS & EDUCATION",
    "certifications": "CERTIFICATIONS & EDUCATION",
    "certs": "CERTIFICATIONS & EDUCATION",
    "licenses": "CERTIFICATIONS & EDUCATION",
    "licenses & certifications": "CERTIFICATIONS & EDUCATION",
    "personal projects": "PROJECTS",
    "selected projects": "PROJECTS",
    "side projects": "PROJECTS",
    "technical projects": "PROJECTS",
    "key projects": "PROJECTS",
    "notable projects": "PROJECTS",
    "open source": "PROJECTS",
    "open-source projects": "PROJECTS",
}


def _canonical_section(text: str) -> str | None:
    """Return the canonical section header for *text* (an exact case-insensitive
    match against `SECTION_HEADERS`, else a known alias), or None when *text* is
    not a section header. Used by both the contact-block boundary detection and
    the section-collection loop so alternate headings resolve consistently."""
    upper = text.upper()
    if upper in SECTION_HEADERS:
        return upper
    return _SECTION_ALIASES.get(text.strip().lower())


@dataclass
class Role:
    title: str
    employer: str
    dates: str
    bullets: list[str] = field(default_factory=list)


@dataclass
class Project:
    """A personal-project entry from the `PROJECTS` docx section. Distinct from
    `Role` (employment): projects are genuine work but not employment, so the
    tailor never renders employer-style metrics on them. Long form lives in
    `kb/profile/work-long-form.md`; claimability notes in
    `kb/profile/verified-notes.md` (both gitignored, agent-reference only)."""

    name: str
    url: str
    stack: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


@dataclass
class VerifiedFacts:
    name: str
    contact_line: str
    summary: str
    skills_core: list[str]
    skills_cms: list[str]
    skills_data_devops: list[str]
    skills_ai: list[str]
    skills_projects: list[str]
    skills_familiar: list[str]
    work_history: list[Role]
    certifications: list[str]
    education: list[str]
    coursework_baseline: list[str]
    projects: list[Project] = field(default_factory=list)


_SKILL_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z &\-]*?):\s*(.+)$")

# Alternate skill-section labels (lowercased) mapped onto the canonical buckets,
# so a resume that does not use Casey's exact headings still populates the right
# bucket. The exact bucket-name match runs first, so the canonical names need no
# self-entry here. Mirrors the `_REGION_EXPANSIONS` alias-map precedent in
# `convert_resume_cmd`. An unrecognized label is warned, never silently dropped.
_SKILL_LABEL_ALIASES: dict[str, str] = {
    "languages": "Core",
    "programming languages": "Core",
    "frameworks": "Core",
    "frameworks & libraries": "Core",
    "languages & frameworks": "Core",
    "languages and frameworks": "Core",
    "libraries": "Core",
    "frontend": "Core",
    "front-end": "Core",
    "databases": "Data & DevOps",
    "data": "Data & DevOps",
    "devops": "Data & DevOps",
    "infrastructure": "Data & DevOps",
    "cloud": "Data & DevOps",
    "cms": "CMS & E-commerce",
    "e-commerce": "CMS & E-commerce",
    "ecommerce": "CMS & E-commerce",
    "ai": "AI & Tooling",
    "ml": "AI & Tooling",
    "ai & ml": "AI & Tooling",
    "ai & automation": "AI & Tooling",
    "ai and automation": "AI & Tooling",
    "automation": "AI & Tooling",
    "tooling": "AI & Tooling",
    "tools": "AI & Tooling",
    "projects": "Project Stack",
    "exposure": "Familiar",
}

# Supports these formats:
#   "Title | Employer\tDates"      (tab-separated — original)
#   "Title | Employer  Dates"      (trailing date after employer, space-separated)
#   "Title | Employer (Dates)"     (date wrapped in parentheses — 2026-06 reformat)
# Dates anchored to a 4-digit year, optionally preceded by a "(" and a month
# abbreviation. The optional month-name prefix is gated to real month
# abbreviations so the employer/dates split doesn't get fooled by a city name
# (or an "(NDA)" employer suffix) preceding the year (e.g. "Multiple Venues,
# Toronto (2015 – 2024)" must split before "(2015", not before "Toronto"; and
# "AI Agency (NDA) (Jan 2026 – Apr 2026)" must keep "(NDA)" in the employer).
# The surrounding parentheses, when present, are kept in the captured `dates`
# so the tailored resume renders them back verbatim.
_MONTH_RE = (
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec|"
    r"January|February|March|April|June|July|August|September|October|November|December)"
)
_ROLE_LINE_RE = re.compile(
    r"^(?P<title>.+?)\s*\|\s*(?P<employer>.+?)\s*"
    rf"(?:\t\s*|\s{{2,}}|\s+(?=\(?(?:{_MONTH_RE}\s+)?\d{{4}}))"
    rf"(?P<dates>\(?(?:{_MONTH_RE}\s+)?\d{{4}}.*)$"
)


def _is_project_header(text: str) -> bool:
    """A PROJECTS-section header ends in a repo URL after a separator.

    Two accepted forms:

    - ``Name | url`` — the original. A line containing ``|`` whose right-hand
      side is a single whitespace-free token.
    - ``Name — Description — url`` — em-dash form (2026-07). Split on the
      *last* em-dash so an em-dash inside the description is not the split
      point, and additionally require the tail to look like a URL (a ``.`` or
      ``/``). Prose bullets in this section routinely contain em-dashes, and
      without the URL test a bullet ending in a single word would be misread
      as a header.

    Bullets are prose: the token after the separator has spaces, so they are
    not mistaken for headers. The ``Stack:`` line is handled separately.
    """
    parts = _split_project_header(text)
    if parts is None:
        return False
    _, right = parts
    if "|" in text:
        return bool(right) and " " not in right
    return bool(right) and " " not in right and ("." in right or "/" in right)


def _split_project_header(text: str) -> tuple[str, str] | None:
    """Split a project header into ``(name, url)``. None when there is no
    separator. Shared by the predicate and its consumer so the two can never
    disagree about where the split falls."""
    if "|" in text:
        name, url = text.split("|", 1)
        return name.strip(), url.strip()
    if "—" in text:
        name, url = text.rsplit("—", 1)
        return name.strip(), url.strip()
    return None


# Generic credential classifier for the CERTIFICATIONS & EDUCATION section.
# Degree vocabulary is checked first so a genuine degree (which never carries
# cert words) wins; a line with cert words but no degree words is a cert. The
# "Associate" cert tier is deliberately NOT matched here ("associate degree" is
# required) so an "AWS ... - Associate" cert is not mis-routed to education.
_DEGREE_RE = re.compile(
    r"\b(?:bachelor|master|doctorate|ph\.?\s?d|m\.?\s?sc|b\.?\s?sc|b\.?\s?eng|"
    r"m\.?\s?eng|b\.?\s?a|associate(?:['’]s)?\s+degree|diploma|university|"
    r"college|honou?rs?|dean(?:['’]s)?\s+list|g\.?p\.?a\.?|cum\s+laude)\b",
    re.IGNORECASE,
)
_CERT_RE = re.compile(
    r"\b(?:certified|certificate|certification|licen[cs]e|credential|badge)\b",
    re.IGNORECASE,
)


def _classify_credential(text: str) -> str | None:
    """Classify a CERTIFICATIONS & EDUCATION line as ``"education"`` or
    ``"cert"`` by generic credential keywords. Returns ``None`` when neither
    vocabulary matches, so the caller defaults to education and warns. Degree
    vocabulary wins when both appear."""
    if _DEGREE_RE.search(text):
        return "education"
    if _CERT_RE.search(text):
        return "cert"
    return None


def _split_skills(value: str) -> list[str]:
    """Split a comma-separated skill list, but treat commas inside parentheses as literal."""
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in value:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            item = "".join(buf).strip()
            if item:
                out.append(item)
            buf = []
        else:
            buf.append(ch)
    item = "".join(buf).strip()
    if item:
        out.append(item)
    return out


def _paragraph_text_with_links(p: Paragraph) -> str:
    """Return paragraph text with hyperlink visible-text replaced by the
    hyperlink's target URL. `mailto:` prefixes are stripped so email addresses
    remain readable. Falls back to visible text if a hyperlink has no
    resolvable relationship target."""
    rels = p.part.rels
    parts: list[str] = []
    for child in p._element.iterchildren():
        tag = child.tag
        if tag == qn("w:hyperlink"):
            rid = child.get(qn("r:id"))
            visible = "".join(t.text or "" for t in child.iter(qn("w:t")))
            target = rels[rid].target_ref if rid and rid in rels else ""
            if not target:
                parts.append(visible)
            elif target.lower().startswith("mailto:"):
                parts.append(target[len("mailto:") :])
            else:
                # Normalize http:// hyperlinks to https://.
                if target.lower().startswith("http://"):
                    target = "https://" + target[len("http://"):]
                parts.append(target)
        elif tag == qn("w:r"):
            parts.append("".join(t.text or "" for t in child.iter(qn("w:t"))))
    return "".join(parts).strip()


def parse_baseline(docx_path: Path) -> tuple[VerifiedFacts, list[str]]:
    if not docx_path.is_file():
        raise PipelineError(f"baseline resume not found: {docx_path}")

    warnings: list[str] = []
    doc = Document(str(docx_path))
    non_empty = [p for p in doc.paragraphs if p.text.strip()]
    if len(non_empty) < 2:
        raise PipelineError(f"baseline resume is empty: {docx_path}")

    paras: list[tuple[str, str]] = [
        ((p.style.name if p.style else ""), p.text.strip()) for p in non_empty
    ]

    name = non_empty[0].text.strip()
    # Contact block: every paragraph after the name up to the first section
    # header. The master may wrap a long contact line onto a second paragraph
    # (links pushed to line 2), so join them rather than reading only line 1.
    _first_section = next(
        (i for i in range(1, len(paras)) if _canonical_section(paras[i][1]) is not None),
        len(paras),
    )
    if _first_section == len(paras):
        warnings.append(
            "no recognized section header found (expected one of "
            f"{sorted(SECTION_HEADERS)} or a known alias); nothing below the "
            "contact block was parsed"
        )
    contact_line = "  ".join(
        _paragraph_text_with_links(p) for p in non_empty[1:_first_section]
    ).strip()

    sections: dict[str, list[tuple[str, str]]] = {h: [] for h in SECTION_HEADERS}
    current: str | None = None
    for style, text in paras[2:]:
        canon = _canonical_section(text)
        if canon is not None:
            current = canon
            continue
        if current is None:
            continue
        sections[current].append((style, text))

    summary = " ".join(t for _, t in sections["SUMMARY"]).strip()

    skill_buckets: dict[str, list[str]] = {
        "Core": [],
        "CMS & E-commerce": [],
        "Data & DevOps": [],
        "AI & Tooling": [],
        "Project Stack": [],
        "Familiar": [],
    }
    for _, text in sections["TECHNICAL SKILLS"]:
        m = _SKILL_LINE_RE.match(text)
        if not m:
            warnings.append(
                f"TECHNICAL SKILLS: line is not in 'Label: items' form, skipped: {text!r}"
            )
            continue
        label, items = m.group(1).strip(), m.group(2).strip()
        low = label.lower()
        # Exact bucket-name match wins; fall back to the alias map for resumes
        # that use alternate skill-section labels.
        bucket = next((b for b in skill_buckets if b.lower() == low), None)
        if bucket is None:
            bucket = _SKILL_LABEL_ALIASES.get(low)
        if bucket is not None:
            skill_buckets[bucket].extend(_split_skills(items))
        else:
            warnings.append(
                f"TECHNICAL SKILLS: unrecognized skill label {label!r}, items dropped: {items!r}"
            )

    work_history: list[Role] = []
    current_role: Role | None = None
    for style, text in sections["PROFESSIONAL EXPERIENCE"]:
        m = _ROLE_LINE_RE.match(text)
        if style == "List Paragraph" or (m is None and "|" not in text):
            # Treat as a bullet: either explicitly styled as one, or doesn't
            # match a role header (some resumes use 'normal' style throughout).
            if current_role is None:
                warnings.append(
                    f"PROFESSIONAL EXPERIENCE: bullet before any role header, skipped: {text!r}"
                )
                continue
            current_role.bullets.append(text)
            continue
        if not m:
            warnings.append(
                f"PROFESSIONAL EXPERIENCE: unparseable role header, skipped: {text!r}"
            )
            continue
        if current_role is not None:
            work_history.append(current_role)
        current_role = Role(
            title=m.group("title").strip(),
            employer=m.group("employer").strip(),
            dates=m.group("dates").strip(),
        )
    if current_role is not None:
        work_history.append(current_role)

    certifications: list[str] = []
    education: list[str] = []
    coursework: list[str] = []
    for _, text in sections["CERTIFICATIONS & EDUCATION"]:
        # The coursework line is education and also seeds the baseline coursework
        # list; handle it before the generic classifier (it carries no degree or
        # cert keyword of its own).
        if "Coursework:" in text:
            _, after = text.split("Coursework:", 1)
            coursework = [c.strip().rstrip(".") for c in after.split(",") if c.strip()]
            education.append(text)
            continue
        kind = _classify_credential(text)
        if kind == "cert":
            certifications.append(text)
        elif kind == "education":
            education.append(text)
        else:
            warnings.append(
                "CERTIFICATIONS & EDUCATION: could not classify as cert or "
                f"education, defaulted to education: {text!r}"
            )
            education.append(text)

    projects: list[Project] = []
    current_project: Project | None = None
    for _, text in sections["PROJECTS"]:
        if text.startswith("Stack:"):
            if current_project is not None:
                current_project.stack = _split_skills(text[len("Stack:") :].strip())
            continue
        if _is_project_header(text):
            name_part, url_part = _split_project_header(text)  # type: ignore[misc]
            # Project urls keep the docx's visible bare-domain form; the
            # hyperlink-target substitution upstream may have swapped in a
            # scheme-prefixed target, so strip the scheme back off here.
            url = url_part.strip()
            for scheme in ("https://", "http://"):
                if url.lower().startswith(scheme):
                    url = url[len(scheme) :]
                    break
            current_project = Project(name=name_part.strip(), url=url)
            projects.append(current_project)
            continue
        # Otherwise a bullet. Orphan bullets before any header are skipped rather
        # than raising — projects are supplementary, a malformed line must not
        # break `convert-resume`.
        if current_project is not None:
            current_project.bullets.append(text)
        else:
            warnings.append(f"PROJECTS: bullet before any project header, skipped: {text!r}")

    facts = VerifiedFacts(
        name=name,
        contact_line=contact_line,
        summary=summary,
        skills_core=skill_buckets["Core"],
        skills_cms=skill_buckets["CMS & E-commerce"],
        skills_data_devops=skill_buckets["Data & DevOps"],
        skills_ai=skill_buckets["AI & Tooling"],
        skills_projects=skill_buckets["Project Stack"],
        skills_familiar=skill_buckets["Familiar"],
        work_history=work_history,
        certifications=certifications,
        education=education,
        coursework_baseline=coursework,
        projects=projects,
    )
    return facts, warnings


def write_verified_json(facts: VerifiedFacts, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(facts)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _md_bullets(items: list[str]) -> str:
    return "\n".join(f"- {b}" for b in items) + ("\n" if items else "")


def write_kb_markdown(facts: VerifiedFacts, kb_dir: Path) -> list[Path]:
    """Regenerate kb/profile/*.md from verified facts. Idempotent — overwrites."""
    profile = kb_dir / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    resume_md = profile / "resume.md"
    resume_md.write_text(
        f"# {facts.name}\n\n{facts.contact_line}\n\n## Summary\n\n{facts.summary}\n",
        encoding="utf-8",
    )
    written.append(resume_md)

    skills_md = profile / "skills.md"
    skills_md.write_text(
        "# Skills\n\n"
        "Core vs Familiar is a hard honesty signal. Tailoring must not promote a Familiar\n"
        "skill into a Core category. See `kb/policies/tailoring-rules.md`.\n\n"
        "## Core\n\n"
        f"{_md_bullets(facts.skills_core)}\n"
        "## CMS & E-commerce\n\n"
        f"{_md_bullets(facts.skills_cms)}\n"
        "## Data & DevOps\n\n"
        f"{_md_bullets(facts.skills_data_devops)}\n"
        "## AI & Tooling\n\n"
        f"{_md_bullets(facts.skills_ai)}\n"
        "## Project Stack\n\n"
        f"{_md_bullets(facts.skills_projects)}\n"
        "## Familiar\n\n"
        f"{_md_bullets(facts.skills_familiar)}",
        encoding="utf-8",
    )
    written.append(skills_md)

    history_lines = ["# Work History\n"]
    for role in facts.work_history:
        history_lines.append(f"## {role.title} — {role.employer}")
        history_lines.append(f"{role.dates}\n")
        history_lines.extend(f"- {b}" for b in role.bullets)
        history_lines.append("")
    history_md = profile / "work-history.md"
    history_md.write_text("\n".join(history_lines), encoding="utf-8")
    written.append(history_md)

    edu_md = profile / "education.md"
    edu_md.write_text(
        "# Certifications & Education\n\n"
        "## Certifications\n\n"
        f"{_md_bullets(facts.certifications)}\n"
        "## Education\n\n"
        f"{_md_bullets(facts.education)}\n"
        "## Baseline coursework line\n\n"
        f"{', '.join(facts.coursework_baseline)}\n",
        encoding="utf-8",
    )
    written.append(edu_md)

    if facts.projects:
        proj_lines = [
            "# Projects\n",
            "Personal projects (genuine work, not employment). No employer-style\n"
            "metrics. See `work-long-form.md` in this directory for the long form.\n",
        ]
        for p in facts.projects:
            proj_lines.append(f"## {p.name}")
            if p.url:
                proj_lines.append(p.url)
            if p.stack:
                proj_lines.append(f"Stack: {', '.join(p.stack)}")
            proj_lines.append("")
            proj_lines.extend(f"- {b}" for b in p.bullets)
            proj_lines.append("")
        projects_md = profile / "projects.md"
        projects_md.write_text("\n".join(proj_lines), encoding="utf-8")
        written.append(projects_md)

    return written
