"""Parse Resume.docx into structured `verified.json` + KB markdown.

The output of this module is the single source of truth for tailoring. Downstream
prompts must only use facts present in `verified.json` — that is the structural
enforcement of the no-fabrication rule from `Resume_Tailoring_Instructions.md` §2.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from jobhunt.errors import PipelineError

SECTION_HEADERS = {
    "SUMMARY",
    "TECHNICAL SKILLS",
    "PROFESSIONAL EXPERIENCE",
    "CERTIFICATIONS & EDUCATION",
}


@dataclass
class Role:
    title: str
    employer: str
    dates: str
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
    skills_familiar: list[str]
    work_history: list[Role]
    certifications: list[str]
    education: list[str]
    coursework_baseline: list[str]


_SKILL_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z &\-]*?):\s*(.+)$")
# Supports two formats:
#   "Title | Employer\tDates"  (tab-separated — original)
#   "Title | Employer  Dates"  (trailing date after employer, space-separated)
# Dates anchored to a 4-digit year so the employer name is captured greedily first.
_ROLE_LINE_RE = re.compile(
    r"^(?P<title>.+?)\s*\|\s*(?P<employer>.+?)\s*(?:\t\s*|\s{2,}|\s+(?=\d{4}))(?P<dates>\d{4}.*)$"
)


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


def _paragraph_text_with_links(p) -> str:
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


def parse_baseline(docx_path: Path) -> VerifiedFacts:
    if not docx_path.is_file():
        raise PipelineError(f"baseline resume not found: {docx_path}")

    doc = Document(str(docx_path))
    non_empty = [p for p in doc.paragraphs if p.text.strip()]
    if len(non_empty) < 2:
        raise PipelineError(f"baseline resume is empty: {docx_path}")

    paras: list[tuple[str, str]] = [
        ((p.style.name if p.style else ""), p.text.strip()) for p in non_empty
    ]

    name = non_empty[0].text.strip()
    contact_line = _paragraph_text_with_links(non_empty[1])

    sections: dict[str, list[tuple[str, str]]] = {h: [] for h in SECTION_HEADERS}
    current: str | None = None
    for style, text in paras[2:]:
        if text.upper() in SECTION_HEADERS:
            current = text.upper()
            continue
        if current is None:
            continue
        sections[current].append((style, text))

    summary = " ".join(t for _, t in sections["SUMMARY"]).strip()

    skill_buckets: dict[str, list[str]] = {
        "Core": [],
        "CMS & E-Commerce": [],
        "Data & DevOps": [],
        "AI & Tooling": [],
        "Familiar": [],
    }
    for _, text in sections["TECHNICAL SKILLS"]:
        m = _SKILL_LINE_RE.match(text)
        if not m:
            continue
        label, items = m.group(1).strip(), m.group(2).strip()
        for bucket in skill_buckets:
            if label.lower() == bucket.lower():
                # AI line uses prose with semicolons + commas; keep it intact rather
                # than splitting on commas. All other categories are clean comma lists.
                if bucket == "AI & Tooling":
                    skill_buckets[bucket].append(items)
                else:
                    skill_buckets[bucket].extend(_split_skills(items))
                break

    work_history: list[Role] = []
    current_role: Role | None = None
    for style, text in sections["PROFESSIONAL EXPERIENCE"]:
        m = _ROLE_LINE_RE.match(text)
        if style == "List Paragraph" or (m is None and "|" not in text):
            # Treat as a bullet: either explicitly styled as one, or doesn't
            # match a role header (some resumes use 'normal' style throughout).
            if current_role is None:
                raise PipelineError(f"orphan bullet before any role header: {text!r}")
            current_role.bullets.append(text)
            continue
        if not m:
            raise PipelineError(f"unparseable role header: {text!r}")
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
        if text.lower().startswith("contentful certified") or "skill badge" in text.lower():
            certifications.append(text)
        elif text.startswith("Dean") or "Coursework" in text:
            if "Coursework:" in text:
                _, after = text.split("Coursework:", 1)
                coursework = [c.strip().rstrip(".") for c in after.split(",") if c.strip()]
            education.append(text)
        else:
            education.append(text)

    return VerifiedFacts(
        name=name,
        contact_line=contact_line,
        summary=summary,
        skills_core=skill_buckets["Core"],
        skills_cms=skill_buckets["CMS & E-Commerce"],
        skills_data_devops=skill_buckets["Data & DevOps"],
        skills_ai=skill_buckets["AI & Tooling"],
        skills_familiar=skill_buckets["Familiar"],
        work_history=work_history,
        certifications=certifications,
        education=education,
        coursework_baseline=coursework,
    )


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
        "## CMS & E-Commerce\n\n"
        f"{_md_bullets(facts.skills_cms)}\n"
        "## Data & DevOps\n\n"
        f"{_md_bullets(facts.skills_data_devops)}\n"
        "## AI & Tooling\n\n"
        f"{_md_bullets(facts.skills_ai)}\n"
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
    return written
