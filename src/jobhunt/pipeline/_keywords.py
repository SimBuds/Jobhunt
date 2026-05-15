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
    key = verified_skill.lower().strip()
    # Strip parenthetical detail: "PostgreSQL (Postgres)" → "postgresql".
    key = re.sub(r"\s*\(.*?\)\s*", "", key).strip()
    family = _PEER_INDEX.get(key)
    if family is None:
        # Try parenthetical contents too: "Shopify (Liquid)" → look up "shopify".
        first_token = key.split()[0] if key else ""
        family = _PEER_INDEX.get(first_token)
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
    }
)


def phrase_tokens(phrase: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(phrase.lower()) if t not in _STOPWORDS and len(t) > 1]


def phrase_present(phrase: str, blob: str) -> bool:
    """Phrase counts as covered if (a) the full phrase appears as a substring,
    or (b) every non-stopword token in the phrase appears somewhere in the blob.
    Blob must already be lower-cased.
    """
    p = phrase.lower().strip()
    if not p:
        return False
    if p in blob:
        return True
    tokens = phrase_tokens(p)
    if not tokens:
        return False
    return all(t in blob for t in tokens)
