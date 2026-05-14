"""Deterministic cover-letter validator.

Mirrors the hard rules in `kb/prompts/cover.md` so that violations are caught
post-decode rather than relying on the LLM to obey the prompt. Run after
`write_cover` and before .docx render. Returns a list of violation strings;
the caller decides whether to abort or warn.
"""

from __future__ import annotations

import re
from typing import Any

from jobhunt.pipeline.cover import CoverLetter

# From cover.md §7. Lowercased; matched as case-insensitive substrings on a
# normalized body so "Passionate" and "passionate" both fire.
BANNED_PHRASES: tuple[str, ...] = (
    "passionate",
    "synergy",
    "leveraged",
    "spearheaded",
    "results-driven",
    "i'm excited",
    "i believe",
    "aligns with",
    "core requirements",
    "production-grade",
    "complementing my practical experience",
    "track record",
    "proven ability",
    "deeply passionate",
    "hit the ground running",
    "value-add",
    "direct match",
    "mirrors the kind of",
    "technical rigor",
    "i'd bring to",
    "i'd welcome the chance",
    "the chance to discuss",
    "i'm drawn to",
    "transform enterprises",
    "support your team's goals",
    "the model transfers",
    "model transfers well",
    "rather than directly",
    "ready to support",
    "deliver immediately",
)


# Defensive gap-volunteering patterns. These are matched as regex on the body,
# not as flat substrings, because they require structural context (e.g.
# "rather than" only counts when it disclaims a tech, not in neutral use).
# Mirrors cover.md rule §4 + §8.
_DEFENSIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    # "coming from React rather than Vue" / "while I have JS rather than Java"
    (r"\b(?:coming from|while i have)\b[^.]*\brather than\b", "defensive 'rather than' gap-volunteering"),
    # "the model transfers" in any disclaiming context
    (r"\bthe model transfers\b", "defensive 'the model transfers' phrasing"),
    # Standalone "rather than <Tech>" claims about Casey's stack
    (r"\bi (?:am )?(?:familiar|comfortable)[^.]*\brather than\b", "defensive familiarity disclaimer"),
    # Formulaic gap-volunteering closer. "I am available to discuss …" is
    # legitimate and not matched; only the "ready to" variant trips, since it
    # signals the model is filling space rather than naming a next step.
    (r"\bi am ready to\b", "formulaic 'I am ready to' closer"),
)

# Form-letter openers banned by §2. Matched after stripping a leading
# "i am " / "i'm " so "I am applying for…" is caught the same as "Applying for…".
BANNED_OPENERS: tuple[str, ...] = (
    "applying for",
    "applying to",
    "writing to",
    "excited to",
    "thrilled to",
    "to whom it may concern",
)

_LEADING_FILLER_RE = re.compile(r"^(?:i\s*am\s+|i'?m\s+|hello,?\s*|hi,?\s*)+", re.IGNORECASE)
_SIGNOFF_TAIL_RE = re.compile(
    r"\b(?:best|regards|sincerely|cheers|thanks|thank you|best regards|kind regards)\s*,?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Phrases that indicate the candidate is disclaiming a tech, not claiming it.
# Used to suppress fabrication false-positives like "rather than Scala".
_NEGATION_PRECEDES_RE = re.compile(
    r"\b(?:not|no|never|without|lack(?:ing)?|rather than|instead of|"
    r"unverified|don['’]?t (?:have|use|know)|haven['’]?t (?:used|worked)|"
    r"unfamiliar with|outside (?:my|of))\b[^.]*$",
    re.IGNORECASE,
)

_DIGIT_CLUSTER_RE = re.compile(r"(?<![A-Za-z\d])\d[\d,.]*(?![A-Za-z\d])")
_WORD_RE = re.compile(r"\b\w+\b")
# Clock-style time references: "11:00 AM", "9 a.m.", "5pm", "12:30". Stripped
# before the digit-cluster pass so the colon-split doesn't fabricate "11"/"00"
# violations from "11:00 AM".
_TIME_OF_DAY_RE = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:[ap]\.?\s*m\.?|am|pm)\b|\b\d{1,2}:\d{2}\b",
    re.IGNORECASE,
)
# Year tokens (and year ranges). Stripped before the digit-cluster pass so
# "2025 to 2026" / "in 2026" don't surface as fabricated numbers — years are
# verifiable from work-history dates in verified.json and the rendered resume.
_YEAR_RANGE_RE = re.compile(
    r"\b20\d{2}(?:\s*(?:to|–|—|-)\s*(?:20\d{2}|present))?\b",
    re.IGNORECASE,
)

# qwen-custom emits curly apostrophes (U+2019) and friends; BANNED_PHRASES use
# ASCII '. Normalize input into ASCII space before matching so phrases like
# "team's goals" / "i'm excited" can't slip past the substring check.
_APOSTROPHE_RE = re.compile(r"[‘’‛ʼ`´]")


def _normalize(text: str) -> str:
    return _APOSTROPHE_RE.sub("'", text).lower()

# Company-name match: drop corporate suffixes, descriptors, and TLD fragments
# so the lead-paragraph check doesn't fail when the model writes "Appnovation"
# instead of "Appnovation Technologies", or "Astra North" instead of
# "Astra North Infoteck Inc.".
_COMPANY_STOPWORDS: frozenset[str] = frozenset({
    "inc", "ltd", "llc", "corp", "corporation", "company", "co",
    "technologies", "technology", "solutions", "systems", "services",
    "group", "holdings", "labs", "studio", "studios", "ventures",
    "the", "and", "of", "for",
    "io", "ai", "com", "net", "org",
})
_COMPANY_SPLIT_RE = re.compile(r"[\s/,&|\-.()]+| and ")


def _body_text(cover: CoverLetter) -> str:
    return "\n\n".join(p for p in cover.body if p).strip()


def _full_text(cover: CoverLetter) -> str:
    return "\n".join([cover.salutation, _body_text(cover), cover.sign_off]).strip()


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


# Tech names frequently fabricated by qwen when mentioned in the JD but
# absent from verified.json — any match in the cover body that isn't in
# the verified skill blob is a hard violation.
_FABRICATION_WATCHLIST: tuple[str, ...] = (
    # Data / infra
    "elasticsearch",
    "kafka",
    "kubernetes",
    "k8s",
    "redis",
    "graphql",
    "terraform",
    "ansible",
    "snowflake",
    "databricks",
    "spark",
    "hadoop",
    # Backend langs / frameworks not in verified
    "rust",
    "golang",
    "scala",
    "ruby",
    "rails",
    "django",
    "flask",
    "fastapi",
    "php",
    "laravel",
    "c#",
    "dotnet",
    # Frontend frameworks not in verified (React + Next are; nothing else is)
    "vue",
    "angular",
    "svelte",
    "nuxt",
    "gatsby",
    "remix",
    "ember",
    "tailwind",
    # Mobile — Casey has no mobile experience
    "kotlin",
    "swift",
    "flutter",
    "react native",
    # Cloud beyond AWS + Azure
    "gcp",
    "google cloud",
    # Enterprise platforms
    "salesforce",
    "servicenow",
    "sap",
    "dynamics 365",
)


def _verified_skill_blob(verified: dict[str, Any]) -> str:
    """Lowercased blob of every verified skill, role text, and project for
    fabrication checks. Includes summary so phrasing like 'Ollama' counts."""
    parts: list[str] = []
    for key in (
        "skills_core",
        "skills_cms",
        "skills_data_devops",
        "skills_ai",
        "skills_familiar",
    ):
        for s in verified.get(key, []):
            parts.append(s)
    if isinstance(verified.get("summary"), str):
        parts.append(verified["summary"])
    for role in verified.get("work_history", []):
        parts.append(role.get("title", ""))
        parts.append(role.get("employer", ""))
        for b in role.get("bullets", []):
            parts.append(b)
    return " ".join(parts).lower()


def _verified_numbers(verified: dict[str, Any]) -> set[str]:
    """Every digit cluster that appears anywhere in verified.json. Used to
    sanity-check that any number in the cover letter has a source."""
    blob_parts: list[str] = []
    for key in ("summary",):
        v = verified.get(key)
        if isinstance(v, str):
            blob_parts.append(v)
    for role in verified.get("work_history", []):
        for b in role.get("bullets", []):
            blob_parts.append(b)
    for key in ("certifications", "education", "coursework_baseline"):
        for line in verified.get(key, []):
            blob_parts.append(line)
    blob = " ".join(blob_parts)
    return set(_DIGIT_CLUSTER_RE.findall(blob))


def validate_cover(
    cover: CoverLetter,
    *,
    verified: dict[str, Any],
    company: str | None,
    max_words: int,
) -> list[str]:
    """Return a list of violation strings. Empty list = clean."""
    violations: list[str] = []
    body = _body_text(cover)
    body_lower = _normalize(body)
    full_lower = _normalize(_full_text(cover))

    for phrase in BANNED_PHRASES:
        if phrase in full_lower:
            violations.append(f"banned phrase: {phrase!r}")

    for pattern, label in _DEFENSIVE_PATTERNS:
        if re.search(pattern, body_lower):
            violations.append(label)

    if cover.body:
        first_lower = _normalize(cover.body[0]).lstrip()
        # Strip leading "I am " / "I'm " / "Hello, " etc. before matching, so
        # "I am applying for…" is caught the same as "Applying for…".
        first_normalized = _LEADING_FILLER_RE.sub("", first_lower).lstrip()
        for opener in BANNED_OPENERS:
            if first_normalized.startswith(opener):
                violations.append(f"form-letter opener: {opener!r}")
                break

    # §2 also: the body must NOT contain a sign-off line. The sign_off field
    # is rendered separately; duplicating it here prints two sign-offs.
    if cover.body:
        for i, para in enumerate(cover.body):
            if _SIGNOFF_TAIL_RE.search(para.strip()):
                violations.append(f"paragraph {i + 1} ends with a sign-off line")
                break

    wc = _word_count(body)
    if wc > max_words:
        violations.append(f"body is {wc} words; max is {max_words}")

    if not (3 <= len(cover.body) <= 4):
        violations.append(f"expected 3-4 paragraphs; got {len(cover.body)}")

    if cover.body and company:
        first_lower = _normalize(cover.body[0])
        raw_tokens = [t.strip().lower() for t in _COMPANY_SPLIT_RE.split(company)]
        company_tokens = [
            t for t in raw_tokens
            if len(t) >= 3 and t not in _COMPANY_STOPWORDS
        ]
        if not company_tokens:
            company_tokens = [company.strip().lower()]
        if not any(t in first_lower for t in company_tokens):
            violations.append(f"lead paragraph does not name company {company!r}")

    # Numeric facts: any digit cluster in the body must trace back to
    # verified.json, with carve-outs:
    # - "30%" → strip to "30" before comparing
    # - bare single digits 1-5 are too generic to flag (they tend to appear in
    #   echoed resume phrases like "3 years")
    # - numbers in the lead paragraph are exempt: the lead typically cites a
    #   JD-stated stat about the company ("1,500 events"), which is reading the
    #   posting back, not fabrication. Numbers in middle/closing paragraphs
    #   describing Casey's work are still checked against verified.json.
    allowed = _verified_numbers(verified)
    body_after_lead = "\n\n".join(cover.body[1:]) if len(cover.body) > 1 else ""
    # Strip clock-style time references first — "11:00 AM", "9 a.m.", "10pm"
    # are reading-back-the-JD, not fabricated metrics. Without this, the digit
    # cluster regex splits "11:00" into "11" and "00" and flags both.
    body_after_lead = _TIME_OF_DAY_RE.sub(" ", body_after_lead)
    body_after_lead = _YEAR_RANGE_RE.sub(" ", body_after_lead)
    for cluster in _DIGIT_CLUSTER_RE.findall(body_after_lead):
        normalized = cluster.rstrip(".,")
        if not normalized:
            continue
        if normalized in allowed:
            continue
        if len(normalized) == 1 and normalized in {"1", "2", "3", "4", "5"}:
            continue
        violations.append(f"unverified number: {cluster!r}")

    # cover.md §5 — no paragraph (except the lead) may recap diploma /
    # coursework. Originally only checked the last paragraph; extended to all
    # non-lead paragraphs because the model started placing recap in paragraph 3
    # of 4 to evade the check.
    _RECAP_TOKENS = ("dean's list", "coursework", "george brown", "diploma")
    if len(cover.body) >= 3:
        for para in cover.body[1:]:  # skip lead
            para_lower = _normalize(para)
            for token in _RECAP_TOKENS:
                if token in para_lower:
                    violations.append(f"body recaps resume material: {token!r}")
                    break
            else:
                continue
            break  # one violation is enough

    sal = _normalize(cover.salutation.strip())
    if "to whom it may concern" in sal:
        violations.append("salutation: 'To whom it may concern' is banned")

    if "!" in body:
        violations.append("body contains an exclamation mark")

    if "{" in body_lower or "}" in body_lower:
        violations.append("body contains an unfilled template placeholder")

    # Fabrication: check the watchlist of frequently-invented techs. If the
    # body claims one and verified.json doesn't, that's a hard violation.
    # Word-boundary match avoids false positives like 'scala' matching
    # 'scalable'. Negation context (e.g. "rather than Scala", "not Scala")
    # is exempt — the model is correctly avoiding the claim, not making it.
    verified_blob = _verified_skill_blob(verified)
    for tech in _FABRICATION_WATCHLIST:
        token = tech.strip(", ")
        if not token:
            continue
        pattern = re.compile(r"\b" + re.escape(token) + r"\b", re.IGNORECASE)
        if not pattern.search(body):
            continue
        if pattern.search(verified_blob):
            continue
        # Check whether every occurrence is in a negation context.
        all_negated = True
        for m in pattern.finditer(body_lower):
            window = body_lower[max(0, m.start() - 40) : m.start()]
            if not _NEGATION_PRECEDES_RE.search(window):
                all_negated = False
                break
        if all_negated:
            continue
        violations.append(f"unverified tech claim: {token!r}")

    return violations
