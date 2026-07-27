"""Deterministic cover-letter validator.

Mirrors the hard rules in `kb/prompts/cover.md` so that violations are caught
post-decode rather than relying on the LLM to obey the prompt. Run after
`write_cover` and before .docx render. Returns a list of violation strings;
the caller decides whether to abort or warn.
"""

from __future__ import annotations

import re
from typing import Any

from jobhunt.pipeline._recap import recap_tokens
from jobhunt.pipeline.cover import CoverLetter

# From cover.md §7. Lowercased; matched as case-insensitive substrings on a
# normalized body so "Passionate" and "passionate" both fire.
# May 2026 trim: dropped "track record" (too generic — fires on legitimate
# sentences like "I've built a track record of shipping Shopify migrations")
# and "production-grade" (used legitimately when describing real deployments).
# Reconsider re-adding either if observed firing on actual letter output.
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
    "complementing my practical experience",
    "proven ability",
    "deeply passionate",
    "hit the ground running",
    "value-add",
    "direct match",
    "matches that need directly",
    "proves this capability",
    "directly mirrors",
    "maps to your roadmap",
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
    (
        r"\b(?:coming from|while i have)\b[^.]*\brather than\b",
        "defensive 'rather than' gap-volunteering",
    ),
    # "the model transfers" in any disclaiming context
    (r"\bthe model transfers\b", "defensive 'the model transfers' phrasing"),
    # Standalone "rather than <Tech>" claims about the candidate's stack
    (
        r"\bi (?:am )?(?:familiar|comfortable)[^.]*\brather than\b",
        "defensive familiarity disclaimer",
    ),
    # "I have also worked with GraphQL concepts" / "exposure to Kubernetes
    # concepts" — the cover talks about a tech as "concepts" because the candidate
    # doesn't actually have hands-on experience with it. The defensive
    # phrasing volunteers a gap the JD didn't ask about; reject structurally
    # so the cover prompt's "silence is stronger than apology" rule is
    # actually enforced.
    (
        r"\b(?:worked with|experience (?:in|with)|exposure to|familiarity with|"
        r"familiar with|knowledge of|understanding of)\s+"
        r"[a-z][a-z0-9+#./-]*\s+concepts\b",
        "defensive 'concepts' framing (gap-volunteering)",
    ),
    # Formulaic gap-volunteering closer. "I am available to discuss …" is
    # legitimate and not matched; only the "ready to" variant trips, since it
    # signals the model is filling space rather than naming a next step.
    (r"\bi am ready to\b", "formulaic 'I am ready to' closer"),
)

# Overconfident one-to-one bridge claims (tone guardrails, 2026-06-25).
# These four started as flat BANNED_PHRASES entries but fired on benign prose
# ("knew exactly where to look", "the export maps directly onto the import
# schema"), causing retry churn. They are matched here as regexes anchored to
# the bridging context the tone guardrails actually ban — a first-person fit
# claim or a claim aimed at the employer's role. The unambiguous proof-language
# phrases ("directly mirrors", "proves this capability", "maps to your
# roadmap") stay flat in BANNED_PHRASES.
_BRIDGE_PATTERNS: tuple[tuple[str, str], ...] = (
    # "this role is exactly where my Shopify work fits" — fit claim; but not
    # "knew exactly where to look first" (no first-person after the phrase).
    (
        r"\bexactly where\b[^.!?]*\b(?:my|i|we)\b",
        "overconfident bridge: 'exactly where' exact-fit claim",
    ),
    # "this mirrors your stack" — employer-aimed; but not "this mirrors the
    # approach I used at Atelier" (self-referential comparison).
    (
        r"\b(?:this|that|it|which) mirrors\b[^.!?]*\byour\b",
        "overconfident bridge: 'this mirrors your …' claim",
    ),
    # "maps directly to your React product work" / "to the role"; but not
    # "the export maps directly onto the import schema".
    (
        r"\bmaps directly\b[^.!?]*\b(?:your?|the role|this role|the position|the job)\b",
        "overconfident bridge: 'maps directly' role-fit claim",
    ),
    (
        r"\btranslates directly\b[^.!?]*\b(?:your?|the role|this role|the position|the job)\b",
        "overconfident bridge: 'translates directly' role-fit claim",
    ),
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

# Framing-level overreach: capability claims (architectural/data-shape phrases)
# that aren't single tech tokens, so they slip past `_FABRICATION_WATCHLIST`.
# Added 2026-05-27 after cover #manual:4bcf846a opened with "...applications in
# TypeScript, Node.js, and Express that handle live data streams and complex
# user workflows" — the candidate has zero live-stream/real-time/WebSocket work in
# verified.json. Each pattern is matched against the cover body; the violation
# is suppressed if the matched phrase already appears in the verified-skill blob
# (so legitimate work passes once added to verified.json) or in a negation
# context (`_NEGATION_PRECEDES_RE`, mirrors the fabrication watchlist).
# Keep this list scoped to recurring overreach categories — don't pile on tech
# tokens here; those belong in `_FABRICATION_WATCHLIST`.
_OVERREACH_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\blive[- ](?:data )?streams?\b", "live data streams"),
    (
        r"\breal[- ]time (?:data )?(?:stream(?:ing)?|processing|pipelines?)\b",
        "real-time streaming/processing",
    ),
    (r"\bwebsockets?\b", "websockets"),
    (
        r"\bevent[- ]driven (?:architectures?|systems?|designs?|pipelines?)\b",
        "event-driven architecture",
    ),
    (r"\bstreaming pipelines?\b", "streaming pipelines"),
    (r"\bdistributed systems?\b", "distributed systems"),
    (r"\bhigh[- ]throughput\b", "high-throughput claim"),
)

# Phrases that indicate the candidate is disclaiming a tech, not claiming it.
# Used to suppress fabrication false-positives like "rather than Scala" or
# "however, I don't have Kubernetes experience". May 2026 additions: "but i
# don't", "though i haven't", "however" — all common disclaiming constructions
# that wrap a tech name without claiming it.
_NEGATION_PRECEDES_RE = re.compile(
    r"\b(?:not|no|never|without|lack(?:ing)?|rather than|instead of|"
    r"unverified|don['’]?t (?:have|use|know)|haven['’]?t (?:used|worked)|"
    r"but (?:i )?don['’]?t|though (?:i )?haven['’]?t|however"
    r"|unfamiliar with|outside (?:my|of))\b[^.]*$",
    re.IGNORECASE,
)

# Boundary class includes `_` (May 2026 fix): without it, qwen's legitimate
# mention of verified tech like `q5_0` (KV-cache quantization name in
# skills_ai) fragments into `q5` + `_` + `0`, and the trailing `0` gets
# flagged as an unverified number. With `_` in the exclusion class, `0` is
# preceded by `_` ∈ class → no match; `q5_0` stays atomic. Legitimate
# standalone `0` (e.g. "0% regression") still flags because it's preceded
# by whitespace/punctuation, not `_`.
_DIGIT_CLUSTER_RE = re.compile(r"(?<![A-Za-z\d_])\d[\d,.]*(?![A-Za-z\d_])")
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
#
# May 2026 refresh:
# - Removed "python" (now Core in verified.json after Phase 1 bucket reshape).
# - Added LLM tooling (langchain, llamaindex, pinecone, weaviate, qdrant,
#   chroma, bedrock, vertex ai) and 2026 JS/TS stack (bun, hono, trpc, prisma,
#   drizzle, astro, sveltekit, qwik). These show up in JDs the model is
#   tempted to claim.
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
    "sveltekit",
    "nuxt",
    "gatsby",
    "remix",
    "astro",
    "qwik",
    "ember",
    "tailwind",
    # 2026 Node/TS server stack — the candidate has Express, not these
    "bun",
    "hono",
    "trpc",
    "prisma",
    "drizzle",
    # Mobile — the candidate has no mobile experience
    "kotlin",
    "swift",
    "flutter",
    "react native",
    # Cloud beyond AWS + Azure
    "gcp",
    "google cloud",
    "vertex ai",
    "bedrock",
    # LLM orchestration / vector DBs — the candidate has Ollama + prompt eng only
    "langchain",
    "llamaindex",
    "haystack",
    "pinecone",
    "weaviate",
    "qdrant",
    "chroma",
    "milvus",
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
        "skills_projects",
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
    # Personal-project narrative (PB5): a cover may anchor on a verified
    # project, so its name, stack, and bullet text must count as verified
    # context — otherwise naming the project's tech (FastAPI, Redis) or
    # describing its build would trip the fabrication watchlist.
    for proj in verified.get("projects", []):
        parts.append(proj.get("name", ""))
        for s in proj.get("stack", []):
            parts.append(s)
        for b in proj.get("bullets", []):
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
    # Personal-project narrative: a cover may cite a number verified in a
    # project bullet ("10 GB GPU"), mirroring _verified_skill_blob's PB5
    # projects fold. Without this, every project-sourced number flags as
    # unverified (8 of 13 revise verdicts in the June 2026 backlog were the
    # same spurious `unverified number: '10'`).
    for proj in verified.get("projects", []):
        blob_parts.append(proj.get("name", ""))
        for s in proj.get("stack", []):
            blob_parts.append(s)
        for b in proj.get("bullets", []):
            blob_parts.append(b)
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

    # Bridge claims are checked on the full text (parity with the flat
    # BANNED_PHRASES entries they replaced).
    for pattern, label in _BRIDGE_PATTERNS:
        if re.search(pattern, full_lower):
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
    #   describing the candidate's work are still checked against verified.json.
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
    tokens = recap_tokens(verified, extra=("coursework",))
    if len(cover.body) >= 3:
        for para in cover.body[1:]:  # skip lead
            para_lower = _normalize(para)
            for token in tokens:
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
        token_pattern = re.compile(r"\b" + re.escape(token) + r"\b", re.IGNORECASE)
        if not token_pattern.search(body):
            continue
        if token_pattern.search(verified_blob):
            continue
        # Check whether every occurrence is in a negation context.
        all_negated = True
        for m in token_pattern.finditer(body_lower):
            window = body_lower[max(0, m.start() - 40) : m.start()]
            if not _NEGATION_PRECEDES_RE.search(window):
                all_negated = False
                break
        if all_negated:
            continue
        violations.append(f"unverified tech claim: {token!r}")

    # Overreach: framing-level capability claims (live data streams, real-time
    # streaming, websockets, etc.) absent from verified.json. Mirrors the
    # fabrication-watchlist structure: word-boundary match in body, suppress
    # if verified_blob already contains the phrase, suppress if every occurrence
    # is in a negation context.
    for pattern_str, label in _OVERREACH_PATTERNS:
        overreach_pattern = re.compile(pattern_str, re.IGNORECASE)
        matches = list(overreach_pattern.finditer(body))
        if not matches:
            continue
        if overreach_pattern.search(verified_blob):
            continue
        all_negated = True
        for m in matches:
            window = body_lower[max(0, m.start() - 40) : m.start()]
            if not _NEGATION_PRECEDES_RE.search(window):
                all_negated = False
                break
        if all_negated:
            continue
        violations.append(f"unverified capability claim: {label!r}")

    return violations


# --- rule-id categorization for `analyze validators` -----------------------
#
# `validate_cover` returns free-text strings ("unverified number: '42'"). For
# aggregation across a window, we need stable rule_ids. `_DEFENSIVE_PATTERNS`
# already carries human-friendly labels (e.g. "defensive: 'rather than X'") so
# we treat the label itself as the rule_id when no other prefix matches.

# Prefix → rule_id mapping. Order doesn't matter; first match wins via the
# longest-prefix scan in `categorize_violation`.
_VIOLATION_PREFIXES: tuple[tuple[str, str], ...] = (
    ("banned phrase:", "banned_phrase"),
    ("form-letter opener:", "banned_opener"),
    ("unverified number:", "unverified_number"),
    ("unverified tech claim:", "unverified_tech"),
    ("unverified capability claim:", "unverified_capability"),
    ("body is", "word_count_over"),
    ("expected 3-4 paragraphs", "paragraph_count"),
    ("lead paragraph does not name company", "company_missing"),
    ("salutation:", "banned_salutation"),
    ("body contains an exclamation mark", "exclamation"),
    ("body contains an unfilled template placeholder", "template_placeholder"),
    ("body recaps resume material:", "recap_in_body"),
    # The sign-off-in-body string is "paragraph N ends with a sign-off line";
    # use a substring match because the leading text varies on N.
    ("ends with a sign-off line", "sign_off_in_body"),
)


def categorize_violation(message: str) -> str:
    """Map a free-text violation message to a stable rule_id.

    Falls back to the literal message lower-cased + space-joined when no
    prefix matches — this captures the `_DEFENSIVE_PATTERNS` labels
    verbatim ("defensive: 'rather than X'") and surfaces them in
    `analyze validators` so over-broad patterns can be tuned.
    """
    msg = message.strip()
    for prefix, rule_id in _VIOLATION_PREFIXES:
        if prefix in msg:
            return rule_id
    # Fall-through: defensive patterns already use stable labels.
    # Lower-case and replace spaces with underscores so the rule_id is
    # SQL/Counter-friendly.
    return msg.lower().replace(" ", "_")[:80]
