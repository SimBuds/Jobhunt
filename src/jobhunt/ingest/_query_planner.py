"""Auto-derive Adzuna search queries from `verified.json`.

Reads skill buckets + work-history bullets and produces a curated list of
Adzuna full-text search strings. Used by `scan_cmd._ingest_all` when the
user leaves `cfg.ingest.adzuna.queries` empty.

Pure-Python, no I/O, no LLM. Deterministic — same verified.json always
produces the same query list. The mapping table is hand-curated rather
than 1:1 from skill names because most skills (Git, Jest, Docker) don't
map to useful search queries on their own; we focus on terms that surface
roles like the candidate's.
"""

from __future__ import annotations

import re
from typing import Any

_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")
_WORD_BOUND_SEO = re.compile(r"\bseo\b", re.IGNORECASE)


def _normalize_skill(s: str) -> str:
    """Lowercase, strip parenthetical suffixes, drop trailing '/x' segments.

    "JavaScript (ES6+)"                       -> "javascript"
    "Shopify (Liquid, Custom Themes)"         -> "shopify"
    "HubSpot CMS (HubL, CRM Integration)"     -> "hubspot cms"
    "CSS3/Sass"                               -> "css3"
    "Contentful (Certified Professional)"     -> "contentful"
    """
    s = _PAREN_RE.sub(" ", s)
    s = s.split("/", 1)[0]
    return s.strip().lower()


# Direct skill→query mapping. Keys must match `_normalize_skill(...)` output.
# Pruned of close subsets (typescript→javascript, next.js→react,
# express→node.js) so high-signal umbrella queries (cms/ai/seo, full stack)
# survive the cap=10 truncation.
_SKILL_QUERIES: dict[str, list[str]] = {
    # Languages
    "javascript": ["javascript developer"],
    "java": ["java developer"],
    "python": ["python developer"],
    # Frontend / backend frameworks
    "react": ["react developer"],
    "node.js": ["node.js developer"],
    "spring boot": ["java developer"],  # collapses into java
    # CMS / e-commerce platforms — each platform individually; the
    # umbrella "cms developer" comes from _CATEGORY_TRIGGERS so it
    # fires whenever skills_cms is non-empty.
    "shopify": ["shopify developer"],
    "hubspot cms": ["hubspot developer"],
    "wordpress": ["wordpress developer"],
    "contentful": ["contentful developer"],
}


def _has_ai_signal(verified: dict[str, Any]) -> bool:
    if verified.get("skills_ai"):
        return True
    for bucket in ("skills_familiar", "skills_core", "skills_data_devops"):
        for item in verified.get(bucket, []) or []:
            low = str(item).lower()
            if "ollama" in low or "llm" in low:
                return True
    return False


def _has_seo_signal(verified: dict[str, Any]) -> bool:
    for role in verified.get("work_history", []) or []:
        for bullet in role.get("bullets", []) or []:
            if _WORD_BOUND_SEO.search(str(bullet)):
                return True
    return False


_CATEGORY_TRIGGERS: list[tuple[Any, list[str]]] = [
    (lambda v: bool(v.get("skills_cms")), ["cms developer"]),
    # CMS / e-commerce implementers are the natural pool for client-facing
    # Solutions / Implementation Engineer roles — the second job family in the
    # specialist lane. Gated on skills_cms like the cms-developer trigger, and
    # placed early so the cap doesn't truncate it. The downstream
    # non-engineering-title drop + score-prompt YoE auto-declines gate any
    # pre-sales/SE roles that aren't a fit, so surfacing them is safe.
    (lambda v: bool(v.get("skills_cms")), ["solutions engineer", "implementation specialist"]),
    (_has_ai_signal,                       ["ai engineer"]),
    # Casey's SEO experience is technical (audits + security hardening), not
    # marketing/content. `seo specialist` returned 5/5 declines on the first
    # auto-derived scan — all non-IC marketing or content roles. Narrowing
    # the query to "technical seo developer" filters to engineering postings.
    (_has_seo_signal,                      ["technical seo developer"]),
]

_BASELINE_QUERIES = ["full stack developer"]

# Phase 6 (reverted): a " Toronto" suffix was added to every derived query
# under the theory that Adzuna's `where=Toronto&distance=100&country=ca`
# location filter was leaking US-mirror postings. Live measurement showed
# the opposite — the geo filter + downstream `is_gta_eligible` allowlist
# were already correct, and the suffix forced Adzuna's full-text matcher
# to AND on "Toronto" which dropped recall ~96% (643 → 25), notably
# zeroing out "react developer" and "javascript developer" which are real
# Toronto markets. The geo filter on the Adzuna call + the GTA allowlist
# downstream is the correct layering; the planner returns un-suffixed
# queries.


def derive_adzuna_queries(verified: dict[str, Any], *, cap: int = 12) -> list[str]:
    """Walk verified.json skills + bullets, return up to `cap` Adzuna queries.

    Order is tuned so the cap doesn't truncate high-signal queries:
      1. Category umbrellas (cms / ai / seo) — these are what the user
         explicitly asked to surface and they cover the platform tail.
      2. Baseline "full stack developer" — load-bearing default.
      3. Direct skill matches from `skills_core` and `skills_cms`.
      4. Direct skill matches from `skills_familiar` / `skills_data_devops`.

    Dedupe is insertion-ordered (case-insensitive); platform-specific
    queries (wordpress/contentful) may fall off the end at cap=12 but
    are absorbed by the broader "cms developer".
    """
    out: list[str] = []

    for predicate, queries in _CATEGORY_TRIGGERS:
        if predicate(verified):
            out.extend(queries)

    out.extend(_BASELINE_QUERIES)

    for bucket in ("skills_core", "skills_cms", "skills_familiar", "skills_data_devops"):
        for raw in verified.get(bucket, []) or []:
            key = _normalize_skill(str(raw))
            for q in _SKILL_QUERIES.get(key, []):
                out.append(q)

    # Case-insensitive dedupe, preserve first occurrence.
    seen: dict[str, None] = {}
    for q in out:
        seen.setdefault(q.lower(), None)
    deduped = list(seen.keys())
    return deduped[:cap]
