"""Shared phrase/keyword matching used by both score-time clamp and post-gen audit.

Kept identical between the two so a phrase that the score pipeline credits as
matched against `verified.json` will also be credited as matched by
`pipeline.audit.keyword_coverage` when it lands in the rendered resume — no
drift between the two checks.

Also exposes `PEER_FAMILIES`: the May 2026 peer-tech family map shared between
the score prompt (which uses it as a transferable-match table) and the audit
fallback (which broadens must-have extraction on short Adzuna snippets). When
the audit can't find verified.json skill X directly in a JD but finds a peer
of X, X is counted as an inferred must-have — the tailor surfaces it under
JD-surface-form rules (tailor.md rule 9).
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9+#./-]+")


# Peer-tech families, May 2026. Each key is a canonical lowercased member; the
# value is the family it belongs to (including itself). Mirrors the families
# listed in kb/prompts/score.md. Keep both sides in sync — the score prompt
# is the human-readable doc; this is the machine-readable index.
#
# Membership rules:
# - One family per key — pick the most-specific. Frontend frameworks live in
#   "frontend", meta-frameworks live in "meta_framework" (Next.js↔Remix), etc.
# - Lowercase, no parenthetical detail. The lookup is substring-aware on the
#   caller's side via `phrase_present`.
PEER_FAMILIES: dict[str, frozenset[str]] = {
    family_name: frozenset(members)
    for family_name, members in {
        "frontend": ("react", "vue", "svelte", "angular", "solidjs", "preact"),
        "meta_framework": (
            "next.js", "nextjs", "remix", "astro", "sveltekit", "nuxt", "qwik",
        ),
        "js_runtime": ("node.js", "node", "nodejs", "bun", "deno"),
        "edge_runtime": (
            "cloudflare workers", "vercel edge", "lambda@edge", "deno deploy",
        ),
        "node_server": ("express", "fastify", "koa", "nestjs", "hono"),
        "orm": ("prisma", "drizzle", "knex", "typeorm", "sequelize", "kysely"),
        "api_pattern": ("rest", "rest api", "restful api", "restful apis", "trpc"),
        "relational_db": (
            "postgres", "postgresql", "mysql", "sqlite", "mariadb", "cockroachdb",
        ),
        "doc_kv_db": ("mongodb", "mongo", "dynamodb", "firestore", "redis"),
        "vector_db": (
            "pinecone", "weaviate", "pgvector", "qdrant", "chroma", "milvus",
        ),
        "js_test_runner": ("jest", "vitest", "mocha", "bun test"),
        "e2e_test_runner": ("playwright", "cypress", "puppeteer", "webdriverio"),
        "cloud_provider": ("aws", "gcp", "azure", "google cloud"),
        "container": ("docker", "podman"),
        "ci": (
            "github actions", "gh actions", "gitlab ci", "circleci",
            "buildkite", "jenkins",
        ),
        "ecommerce": ("shopify", "bigcommerce", "woocommerce", "medusa"),
        "headless_cms": (
            "contentful", "strapi", "sanity", "ghost", "payload", "storyblok",
        ),
        "ai_sdk": (
            "openai", "anthropic", "bedrock", "vertex ai", "ollama",
        ),
        "llm_orchestration": ("langchain", "llamaindex", "haystack", "dspy"),
    }.items()
}


# Reverse index: each member tech maps to the set of all its peers (including
# itself). Used by `peer_match` for O(1) family lookup. Built once at import.
_PEER_INDEX: dict[str, frozenset[str]] = {}
for _members in PEER_FAMILIES.values():
    for _m in _members:
        _PEER_INDEX[_m] = _members


def peer_family_of(verified_skill: str) -> frozenset[str] | None:
    """Return the peer family (frozenset of member techs) that `verified_skill`
    belongs to, or None if it isn't in any registered family.

    Resolves parenthetical detail at lookup time:
      "PostgreSQL (Postgres)" → looks up "postgresql"
      "Shopify (Liquid)"      → falls back to "shopify"

    Phase 10.1: audit's `_extract_must_haves_from_jd` uses this to dedupe
    peer-broadened additions — if AWS is already directly matched, suppress
    adding Azure as an inferred must-have via the `cloud_provider` family.
    """
    key = verified_skill.lower().strip()
    key = re.sub(r"\s*\(.*?\)\s*", "", key).strip()
    family = _PEER_INDEX.get(key)
    if family is None:
        first_token = key.split()[0] if key else ""
        family = _PEER_INDEX.get(first_token)
    return family


def peer_match(verified_skill: str, jd_blob_lower: str) -> bool:
    """True if `jd_blob_lower` mentions any peer of `verified_skill` (per
    PEER_FAMILIES), OR if it mentions `verified_skill` itself. The JD blob
    must already be lower-cased.

    Used by audit fallback to count a verified skill as an inferred must-have
    when the JD names a peer technology. Example: verified has "React", JD
    body mentions "Vue.js" → returns True; the tailor's surface-form rule
    will write the JD's term ("Vue") in the rendered output where appropriate.

    Returns False when `verified_skill` has no peer family registered — in
    that case the caller falls back to plain substring presence via
    `phrase_present`.
    """
    family = peer_family_of(verified_skill)
    if family is None:
        return False
    return any(peer in jd_blob_lower for peer in family)
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "or", "the", "of", "in", "on", "to", "for", "with",
        "is", "are", "be", "as", "at", "by", "it", "this", "that", "you",
        "we", "our", "your", "their", "from", "have", "has", "will", "can",
        "year", "years", "experience", "skills", "knowledge", "ability",
        "strong", "good", "great", "able", "must", "should", "would",
        "preferred", "plus", "bonus", "required", "required.",
        # A JD writes "CI/CD pipelines" where a resume writes "CI/CD": the
        # noun carries no technical discrimination of its own, so requiring it
        # makes a real match unfindable (9 such gaps in the 2026-08-29
        # backlog). Kept to this one word-pair on purpose. "tools",
        # "frameworks" and "technologies" were tried and reverted — they are
        # exactly what makes a vague ask vague, and dropping them let the
        # Pigment regression ("AI/LLM tools", guarded by
        # test_verify_demotes_llm_matched_when_not_in_profile) score as
        # matched against a profile that never claimed it.
        "pipeline", "pipelines",
    }
)


def phrase_tokens(phrase: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(phrase.lower()) if t not in _STOPWORDS and len(t) > 1]


_PAREN_RE = re.compile(r"\([^)]*\)")

# Minimum length for a '/'-part to stand alone as an alternative. Keeps
# 'CI/CD' one unit ('ci'/'cd' are fragments of a single concept) while
# 'CSS3/Sass' and 'Git/GitHub' split into real alternatives.
_MIN_ALT_LEN = 3


def _strip_parenthetical(phrase: str) -> str:
    """Drop parenthetical qualifiers before matching. Verified skills carry
    detail ('WordPress (Elementor)'); score-LLM must-haves carry commentary
    ('WordPress (exact match)'). Neither is part of the keyword itself, and
    requiring commentary tokens makes real matches unfindable — a resume
    listing WordPress can never contain 'exact match'. Falls back to the
    original phrase when stripping leaves nothing."""
    stripped = _PAREN_RE.sub(" ", phrase).strip()
    return stripped or phrase


# `React` / `React.js` / `ReactJS` are three spellings of one technology, and
# JDs use all three interchangeably. `_TOKEN_RE` keeps `.`, so `react.js` is a
# single token that is not a substring of a blob containing `react` — which
# variant a JD happened to use decided whether a verified skill counted. The
# base must be >= _MIN_ALT_LEN so a bare suffix never stands alone as a match.
_JS_SUFFIX_RE = re.compile(rf"^([a-z0-9+#-]{{{_MIN_ALT_LEN},}})(?:\.js|js)$")


def _surface_variants(token: str) -> tuple[str, ...]:
    """Spellings of one technology: base, base.js, basejs.

    Applied in both directions, so a `React.js` must-have matches a resume
    listing `React`, and a `React` must-have matches a resume listing
    `React.js`. Only the `.js`/`js` suffix is folded — it is the one suffix
    that names no distinct technology of its own.
    """
    m = _JS_SUFFIX_RE.match(token)
    base = m.group(1) if m else token
    return (token, base, f"{base}.js", f"{base}js")


def _token_present(token: str, blob: str) -> bool:
    """Substring presence with '/'-compound handling: a token like
    'css3/sass' names alternatives, but a resume lists the parts as separate
    items, so the token also counts when any part >= _MIN_ALT_LEN chars is
    present. Shorter parts ('ci/cd') keep whole-token semantics.

    `.js`/`js` spelling variants are folded first via `_surface_variants`."""
    if any(v in blob for v in _surface_variants(token)):
        return True
    if "/" in token:
        return any(p in blob for p in token.split("/") if len(p) >= _MIN_ALT_LEN)
    return False


def _all_tokens_present(phrase: str, blob: str) -> bool:
    tokens = phrase_tokens(phrase)
    if not tokens:
        return False
    return all(_token_present(t, blob) for t in tokens)


def phrase_present(phrase: str, blob: str) -> bool:
    """Phrase counts as covered if, after dropping parenthetical qualifiers:
    (a) the phrase appears as a substring, or (b) every non-stopword token
    appears somewhere in the blob ('/'-compound tokens match on any part >=
    _MIN_ALT_LEN chars), or (c) any '/'-separated alternative of the whole
    phrase ('Performance Optimization/Core Web Vitals') matches on its own.
    Blob must already be lower-cased.
    """
    p = phrase.lower().strip()
    if not p:
        return False
    if p in blob:
        return True
    p = _strip_parenthetical(p)
    if p in blob or _all_tokens_present(p, blob):
        return True
    if "/" in p:
        return any(
            alt in blob or _all_tokens_present(alt, blob)
            for alt in (a.strip() for a in p.split("/"))
            if len(alt) >= _MIN_ALT_LEN
        )
    return False
