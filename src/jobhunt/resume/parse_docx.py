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


# Skill label charset is deliberately permissive: real resumes write labels like
# "Version Control, CI/CD & Testing", "Databases & Infrastructure", "AI/ML".
# Commas, slashes, plus signs, dots, and digits all occur. The colon is what
# actually delimits label from items, so the label side stays broad and only
# excludes the colon itself.
# Canonical style label for a bullet. Real-world resumes carry any style name on
# a genuine list item, so `parse_baseline` rewrites every numbered/bulleted
# paragraph to this value and the section parsers only ever compare against it.
_LIST_STYLE = "List Paragraph"


def _is_list_item(paragraph: Paragraph) -> bool:
    """True when the paragraph is a real Word list item.

    Keys on the `<w:numPr>` numbering property rather than the style name.
    That property is what actually makes Word render a bullet, and it is stable
    across editors and restyles; the style *name* is not.
    """
    p_pr = paragraph._p.find(qn("w:pPr"))
    if p_pr is None:
        return False
    return p_pr.find(qn("w:numPr")) is not None


_SKILL_LINE_RE = re.compile(r"^([A-Za-z][^:]*?):\s*(.+)$")

# Bucket inference by keyword, replacing a hand-maintained allow-list of exact
# labels. An allow-list can never be complete — the 2026-07-25 resume reformat
# alone introduced six unseen labels and silently dropped every one. Tokens are
# matched against these sets in order; the FIRST bucket with a hit wins.
#
# Familiar is tested first on purpose. Mis-filing an "Additional" or "Exposure"
# row into a Core bucket would promote academic exposure into claimable
# production skill, which is the one bucket error the honesty rules treat as
# fabrication (`kb/policies/tailoring-rules.md`, Core vs Familiar).
_BUCKET_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Familiar", (
        "familiar", "additional", "exposure", "other", "academic", "beginner",
        "basic", "learning", "coursework", "supplementary",
    )),
    ("AI & Tooling", (
        "ai", "ml", "llm", "genai", "automation", "agent", "agentic", "assisted",
        "prompt", "intelligence",
    )),
    ("CMS & E-commerce", (
        "cms", "commerce", "ecommerce", "shopify", "wordpress", "content", "seo",
    )),
    ("Data & DevOps", (
        "database", "data", "devops", "infrastructure", "cloud", "version",
        "control", "testing", "test", "qa", "deployment", "ops", "ci", "cd",
        "platform", "tooling", "tools",
    )),
    ("Core", (
        "language", "framework", "frontend", "backend", "programming",
        "development", "develop", "api", "library", "web", "software", "stack",
        "mobile", "engineering", "technical",
    )),
)

# Rows that are legitimately NOT skills. Without this, the "unknown label falls
# back to Core" rule would file "Climbing, Chess" from an Interests row as
# claimable production skill — worse than dropping it, because the tailor treats
# everything in `verified.json` as fair game.
_NON_SKILL_LABEL_TOKENS: frozenset[str] = frozenset({
    "hobbies", "hobby", "interests", "activities", "references", "awards",
    "volunteer", "volunteering", "publications", "memberships", "affiliations",
    "spoken", "availability", "objective",
})

_LABEL_TOKEN_RE = re.compile(r"[a-z0-9+#]+")


def _is_non_skill_label(label: str) -> bool:
    """True for rows that are not skills at all (Interests, Awards, …)."""
    tokens = set(_LABEL_TOKEN_RE.findall(label.lower()))
    return bool(tokens & _NON_SKILL_LABEL_TOKENS)


def _infer_skill_bucket(label: str) -> str | None:
    """Map an arbitrary skill-row label onto a canonical bucket.

    Token-based rather than substring-based so short keywords cannot match
    inside unrelated words ("ai" must not fire on "available"). A token also
    matches a keyword it merely pluralises or extends ("databases" -> "database",
    "frameworks" -> "framework"), but only for keywords long enough that the
    prefix is meaningful.
    """
    tokens = _LABEL_TOKEN_RE.findall(label.lower())
    if not tokens:
        return None
    for bucket, keywords in _BUCKET_KEYWORDS:
        for token in tokens:
            for kw in keywords:
                if token == kw or token == f"{kw}s":
                    return bucket
                if len(kw) >= 4 and token.startswith(kw):
                    return bucket
    return None

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


# A role header ends in a date range. Anchoring on the dates and splitting what
# precedes them is shape-agnostic, which a single monolithic pattern cannot be:
# resumes write two-pipe, three-pipe (location in the employer cell), pipe-less
# em-dash, and tab-separated headers, and the same person changes shape between
# drafts. The separator before the dates must be a tab, 2+ spaces, or a single
# space followed by an explicit month — a bare "\s+\d{4}" would match mid-bullet
# ("...shipped in 2024 and moved on") and turn prose into a phantom role.
_DATES_TAIL_RE = re.compile(
    rf"(?:\t\s*|\s{{2,}}|\s+(?=\(?{_MONTH_RE}\s+\d{{4}}))"
    rf"(?P<dates>\(?(?:{_MONTH_RE}\s+)?\d{{4}}\b.*)$"
)


def _parse_role_header(text: str) -> tuple[str, str, str] | None:
    """Return ``(title, employer, dates)`` for a role header, else None.

    Accepted shapes, all observed in real resumes::

        Title | Employer | Dates              (location usually inside employer)
        Title | Employer, Location  Dates
        Title | Employer<TAB>Dates
        Employer — Descriptor  Dates          (title-less; title comes back "")

    A title-less header is preserved rather than rejected: dropping it discards
    the whole role and every bullet under it. The caller can see the empty title
    and decide; losing the employer and dates is strictly worse.
    """
    m = _DATES_TAIL_RE.search(text)
    if m is None:
        return None
    dates = m.group("dates").strip()
    head = text[: m.start()].strip()
    if not head:
        return None
    parts = [p.strip() for p in head.split("|") if p.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        return "", parts[0], dates
    # 3+ cells: everything after the title is employer/location detail. Rejoin
    # with commas so a stray separator never survives into `employer`, which is
    # half the identity key the fabrication guard compares on.
    return parts[0], ", ".join(parts[1:]), dates


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
    # rsplit on both forms: the URL is always the LAST cell, and a descriptor
    # cell may sit between name and URL ("Jobhunt | AI Job-Search | github.com/…").
    # Splitting on the first "|" made that three-cell form unparseable, which
    # silently dropped every project in the 2026-07-26 resume.
    if "|" in text:
        name, url = text.rsplit("|", 1)
        return name.strip(), url.strip()
    if "—" in text:
        name, url = text.rsplit("—", 1)
        return name.strip(), url.strip()
    return None


def _project_display_name(name_cell: str) -> str:
    """First cell of a project header — the name, without any descriptor cell."""
    return name_cell.split("|")[0].strip() or name_cell.strip()


# Generic credential classifier for the CERTIFICATIONS & EDUCATION section.
# Degree vocabulary is checked first so a genuine degree (which never carries
# cert words) wins; a line with cert words but no degree words is a cert. The
# "Associate" cert tier is deliberately NOT matched here ("associate degree" is
# required) so an "AWS ... - Associate" cert is not mis-routed to education.
_DEGREE_RE = re.compile(
    r"\b(?:bachelor|master|doctorate|ph\.?\s?d|m\.?\s?sc|b\.?\s?sc|b\.?\s?eng|"
    r"m\.?\s?eng|b\.?\s?a|associate(?:['’]s)?\s+degree|diploma|university|"
    r"college|polytechnic|institute|academy|seminary|"
    r"honou?rs?|dean(?:['’]s)?\s+list|g\.?p\.?a\.?|cum\s+laude|"
    # Academic-detail lines that sit under an education entry. Without these a
    # "Capstone: …" or "Thesis: …" line has neither degree nor cert vocabulary
    # and falls through to the unclassified branch, warning on every parse.
    r"capstone|thesis|practicum|coursework|major|minor|specializ(?:ation|ed))\b",
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

    # Normalise every real list item to the style name the section parsers key
    # on. A paragraph can be a genuine bullet (`<w:numPr>` in its properties)
    # while carrying any style name at all — Word, LibreOffice, and Google Docs
    # all name their list styles differently, and re-styling a resume renames
    # them without changing the list formatting. Detecting the numbering
    # property instead of the label is what makes bullet detection survive a
    # reformat; matching on "List Paragraph" alone silently reclassified every
    # bullet as body text when this resume was restyled on 2026-07-26.
    paras: list[tuple[str, str]] = [
        (_LIST_STYLE if _is_list_item(p) else (p.style.name if p.style else ""),
         p.text.strip())
        for p in non_empty
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
        # Exact bucket name wins, then the curated alias map, then keyword
        # inference. Inference is last so hand-tuned mappings always beat it.
        bucket = next((b for b in skill_buckets if b.lower() == low), None)
        if bucket is None:
            bucket = _SKILL_LABEL_ALIASES.get(low)
        if bucket is None:
            bucket = _infer_skill_bucket(label)
        if bucket is not None:
            skill_buckets[bucket].extend(_split_skills(items))
        elif _is_non_skill_label(label):
            # Interests / hobbies / awards rows: genuinely not skills. Dropping
            # is correct here — filing them as Core would let the tailor claim
            # "Chess" as production experience.
            warnings.append(
                f"TECHNICAL SKILLS: non-skill label {label!r}, items dropped: {items!r}"
            )
        else:
            # Unknown but plausibly a skill row: keep it. The old behaviour
            # discarded it, which is how a reformat wiped every core skill from
            # verified.json without failing loudly. Worded as "assigned", not
            # "dropped", so `convert-resume`'s data-loss guard stays advisory
            # here instead of blocking the write.
            skill_buckets["Core"].extend(_split_skills(items))
            warnings.append(
                f"TECHNICAL SKILLS: unrecognized skill label {label!r}, "
                f"assigned to Core — add an alias if that is wrong"
            )

    work_history: list[Role] = []
    current_role: Role | None = None
    for style, text in sections["PROFESSIONAL EXPERIENCE"]:
        parsed = _parse_role_header(text)
        if style == "List Paragraph" or (parsed is None and "|" not in text):
            # Treat as a bullet: either explicitly styled as one, or doesn't
            # match a role header (some resumes use 'normal' style throughout).
            if current_role is None:
                warnings.append(
                    f"PROFESSIONAL EXPERIENCE: bullet before any role header, skipped: {text!r}"
                )
                continue
            current_role.bullets.append(text)
            continue
        if parsed is None:
            warnings.append(
                f"PROFESSIONAL EXPERIENCE: unparseable role header, skipped: {text!r}"
            )
            continue
        if current_role is not None:
            work_history.append(current_role)
        title, employer, dates = parsed
        current_role = Role(title=title, employer=employer, dates=dates)
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
            current_project = Project(name=_project_display_name(name_part), url=url)
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
