"""Unit tests for the Adzuna query planner."""

from __future__ import annotations

import json
from pathlib import Path

from jobhunt.ingest._query_planner import (
    _has_ai_signal,
    _has_seo_signal,
    _normalize_skill,
    derive_adzuna_queries,
)

VERIFIED_PATH = Path(__file__).parent.parent / "kb" / "profile" / "verified.json"


def test_normalize_skill_strips_parens_and_trailing_slash() -> None:
    assert _normalize_skill("JavaScript (ES6+)") == "javascript"
    assert _normalize_skill("Shopify (Liquid, Custom Themes)") == "shopify"
    assert _normalize_skill("HubSpot CMS (HubL, CRM Integration)") == "hubspot cms"
    assert _normalize_skill("CSS3/Sass") == "css3"
    assert _normalize_skill("Contentful (Certified Professional)") == "contentful"


def test_derive_from_current_baseline() -> None:
    """The live verified.json must produce all user-named umbrella queries."""
    verified = json.loads(VERIFIED_PATH.read_text(encoding="utf-8"))
    qs = derive_adzuna_queries(verified)
    assert len(qs) <= 10
    # Umbrella signals the user explicitly called out.
    for required in (
        "cms developer",
        "ai engineer",
        "seo specialist",
        "javascript developer",
        "java developer",
        "react developer",
        "full stack developer",
    ):
        assert required in qs, f"missing required query: {required!r} in {qs}"


def test_dedupes_collisions() -> None:
    """Spring Boot collapses into 'java developer'; Java present too → one entry."""
    v = {"skills_core": ["Java", "Spring Boot"]}
    qs = derive_adzuna_queries(v)
    assert qs.count("java developer") == 1


def test_empty_skills_returns_only_baseline() -> None:
    qs = derive_adzuna_queries({})
    assert qs == ["full stack developer"]


def test_seo_trigger_requires_bullet_mention() -> None:
    base = {"skills_core": ["Java"]}
    assert "seo specialist" not in derive_adzuna_queries(base)
    base_with_seo = {
        "skills_core": ["Java"],
        "work_history": [{"bullets": ["Ran technical SEO audits."]}],
    }
    assert "seo specialist" in derive_adzuna_queries(base_with_seo)


def test_ai_trigger_via_skills_ai_or_familiar() -> None:
    assert not _has_ai_signal({})
    assert _has_ai_signal({"skills_ai": ["Local LLM hosting"]})
    assert _has_ai_signal({"skills_familiar": ["Ollama via Arch Linux"]})
    assert not _has_ai_signal({"skills_familiar": ["Python"]})


def test_seo_signal_word_boundary() -> None:
    # 'seoul' must not count as 'seo'.
    assert not _has_seo_signal({"work_history": [{"bullets": ["Toured Seoul last year."]}]})
    assert _has_seo_signal({"work_history": [{"bullets": ["Did SEO work."]}]})


def test_cms_trigger_only_when_skills_cms_present() -> None:
    qs = derive_adzuna_queries({"skills_core": ["Java"]})
    assert "cms developer" not in qs
    qs = derive_adzuna_queries({"skills_cms": ["Shopify"]})
    assert "cms developer" in qs
