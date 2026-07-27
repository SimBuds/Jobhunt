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

# The fictional profile, never the repo's own kb/profile/verified.json — that
# file is personal, gitignored, and hand-edited, so asserting against it makes
# the suite pass or fail on whose resume is checked out (IMPLEMENT.md A10/D2).
FIXTURE_PROFILE = (
    Path(__file__).resolve().parent / "fixtures" / "profile" / "verified.json"
)


def test_normalize_skill_strips_parens_and_trailing_slash() -> None:
    assert _normalize_skill("JavaScript (ES6+)") == "javascript"
    assert _normalize_skill("Shopify (Liquid, Custom Themes)") == "shopify"
    assert _normalize_skill("HubSpot CMS (HubL, CRM Integration)") == "hubspot cms"
    assert _normalize_skill("CSS3/Sass") == "css3"
    assert _normalize_skill("Contentful (Certified Professional)") == "contentful"


def test_derive_from_fixture_profile() -> None:
    """A full profile produces the expected umbrella queries.

    Runs against the fictional fixture, so the expectations are stable: a resume
    edit changes the real profile weekly, and this test is about the planner's
    logic, not about one person's current skill list.

    Java and Spring Boot sit in `skills_familiar` in the fixture, so
    'java developer' must NOT appear — searching Familiar tech surfaces roles
    the scorer would decline. That exclusion is the load-bearing assertion here.
    """
    verified = json.loads(FIXTURE_PROFILE.read_text(encoding="utf-8"))
    qs = derive_adzuna_queries(verified)

    assert len(qs) <= 12
    for required in (
        "cms developer",
        "solutions engineer",
        "implementation specialist",
        "ai engineer",
        "javascript developer",
        "react developer",
        "node.js developer",
        "shopify developer",
        "full stack developer",
    ):
        assert required in qs, f"missing required query: {required!r} in {qs}"

    # Familiar-only tech must never become a search query.
    assert "java developer" not in qs
    assert "angular developer" not in qs


def test_seo_query_is_gated_on_work_history_evidence() -> None:
    """'technical seo developer' needs SEO in a *bullet*, not in a skills row.

    `_has_seo_signal` scans work-history bullets only, so the query is gated on
    demonstrated work rather than a claimed skill — listing "technical SEO" in a
    skills row is not enough to start searching SEO roles. Asserting both halves
    documents that deliberately.
    """
    verified = json.loads(FIXTURE_PROFILE.read_text(encoding="utf-8"))
    assert "technical seo developer" not in derive_adzuna_queries(verified)

    # A skills-row claim alone must NOT flip it on.
    claimed_only = {
        **verified,
        "skills_cms": [*verified["skills_cms"], "technical SEO (Core Web Vitals)"],
    }
    assert "technical seo developer" not in derive_adzuna_queries(claimed_only)

    # Evidence in a bullet does.
    with_evidence = {
        **verified,
        "work_history": [
            {
                "title": "Developer",
                "employer": "Acme",
                "dates": "2024",
                "bullets": ["Ran technical SEO audits and lifted Core Web Vitals."],
            }
        ],
    }
    assert "technical seo developer" in derive_adzuna_queries(with_evidence)


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
    assert "technical seo developer" not in derive_adzuna_queries(base)
    base_with_seo = {
        "skills_core": ["Java"],
        "work_history": [{"bullets": ["Ran technical SEO audits."]}],
    }
    assert "technical seo developer" in derive_adzuna_queries(base_with_seo)


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


def test_solutions_eng_queries_gated_on_skills_cms() -> None:
    """Solutions/Implementation Engineer queries surface only for CMS profiles.

    These cover the second job family in the specialist lane (client-facing
    solutions/implementation roles). They are gated on skills_cms presence, same
    as 'cms developer', so a CMS-less profile never sees them.
    """
    cms_less = derive_adzuna_queries({"skills_core": ["Java"]})
    assert "solutions engineer" not in cms_less
    assert "implementation specialist" not in cms_less
    with_cms = derive_adzuna_queries({"skills_cms": ["Shopify"]})
    assert "solutions engineer" in with_cms
    assert "implementation specialist" in with_cms


def test_no_location_suffix_appended() -> None:
    """Phase 6 reverted: queries must not carry a ' Toronto' suffix. Adzuna's
    where=Toronto&distance=100&country=ca + downstream is_gta_eligible
    allowlist handle location; forcing 'Toronto' into the `what` field
    dropped real Toronto-market queries (react/javascript) to zero recall.
    """
    verified = json.loads(FIXTURE_PROFILE.read_text(encoding="utf-8"))
    qs = derive_adzuna_queries(verified)
    for q in qs:
        assert not q.endswith(" Toronto"), f"unexpected Toronto suffix on {q!r}"
