"""Tests for pipeline.audit — keyword coverage + verdict logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunt.pipeline.audit import AuditResult, audit, keyword_coverage
from jobhunt.pipeline.cover import CoverLetter
from jobhunt.pipeline.score import ScoreResult
from jobhunt.pipeline.tailor import TailoredCategory, TailoredResume, TailoredRole

VERIFIED_PATH = Path(__file__).parent.parent / "kb" / "profile" / "verified.json"

_MUST_HAVES = ["TypeScript", "React", "Node.js", "GitHub Actions", "Shopify"]


@pytest.fixture
def verified() -> dict:
    if VERIFIED_PATH.is_file():
        return json.loads(VERIFIED_PATH.read_text())
    return {
        "summary": "Full-stack developer with 2+ years experience.",
        "work_history": [
            {
                "employer": "Custom Jewelry Brand (NDA)",
                "dates": "2023 – Present",
                "bullets": ["Built 14+ page Shopify storefront with 200+ SKUs serving 500+ monthly visitors."],
            },
            {
                "employer": "AI Agency (NDA)",
                "dates": "2026",
                "bullets": ["Cut page load time by 30%."],
            },
            {
                "employer": "Vintage Gaming Retailer (NDA)",
                "dates": "2024",
                "bullets": ["Built custom Shopify page layouts."],
            },
            {
                "employer": "Multiple Venues, Toronto",
                "dates": "2015 – 2024",
                "bullets": ["Led teams of 5–20."],
            },
        ],
        "skills_core": ["TypeScript", "React", "Node.js"],
        "skills_cms": ["Shopify (Liquid, Custom Themes)"],
        "skills_data_devops": ["GitHub Actions CI/CD"],
        "skills_ai": [],
        "skills_familiar": ["Python"],
    }


def _minimal_tailored(verified: dict) -> TailoredResume:
    return TailoredResume(
        summary="TypeScript and React developer with Shopify and Node.js experience.",
        skills_categories=[
            TailoredCategory("Languages", ["TypeScript", "React", "Node.js"]),
            TailoredCategory("DevOps", ["GitHub Actions CI/CD"]),
            TailoredCategory("CMS", ["Shopify (Liquid, Custom Themes)"]),
            TailoredCategory("Familiar", ["Python"]),
        ],
        roles=[TailoredRole(**r) for r in [
            {"title": "Web Developer (Contract)", "employer": "Custom Jewelry Brand (NDA)", "dates": "2023 – Present", "bullets": ["Built Shopify storefront."]},
            {"title": "Web Developer (Contract)", "employer": "AI Agency (NDA)", "dates": "2026", "bullets": ["Built HubSpot theme."]},
            {"title": "Web Developer (Contract)", "employer": "Vintage Gaming Retailer (NDA)", "dates": "2024", "bullets": ["Built Shopify layouts."]},
            {"title": "Sous Chef & Team Lead", "employer": "Multiple Venues, Toronto", "dates": "2015 – 2024", "bullets": ["Led culinary teams."]},
        ]],
        certifications=["Contentful Certified Professional"],
        education=["Computer Programming & Analysis, George Brown College (April 2024)"],
        coursework=["Full-Stack Development", "DevOps"],
        model="test",
    )


def _good_cover(company: str = "Acme Corp") -> CoverLetter:
    return CoverLetter(
        salutation="Dear Hiring Team,",
        body=[
            f"I applied to {company} after reading about the TypeScript and React role. The Shopify angle matches my contract work closely.",
            "The centrepiece project is the 14+ page Shopify storefront I built and maintained for a custom jewellery client over 2+ years.",
            "At an AI agency I built a HubSpot theme from scratch and cut page load time by 30%, setting up GitHub Actions CI before handoff.",
            "Happy to discuss further.",
        ],
        sign_off="Best,\nCasey Hsu",
        model="test",
    )


def _score(must_haves: list[str] | None = None) -> ScoreResult:
    return ScoreResult(
        score=85,
        matched_must_haves=must_haves or _MUST_HAVES,
        gaps=[],
        decline_reason=None,
        ai_bonus_present=False,
        model="test",
    )


# --- keyword_coverage ---


def test_keyword_coverage_all_present(verified: dict) -> None:
    tailored = _minimal_tailored(verified)
    pct, matched, missing = keyword_coverage(_MUST_HAVES, tailored)
    assert pct == 100
    assert missing == []


def test_keyword_coverage_partial(verified: dict) -> None:
    tailored = _minimal_tailored(verified)
    pct, matched, missing = keyword_coverage(["TypeScript", "Angular", "Vue"], tailored)
    assert "TypeScript" in matched
    assert "Angular" in missing
    assert "Vue" in missing
    assert pct < 50


def test_keyword_coverage_empty_must_haves(verified: dict) -> None:
    tailored = _minimal_tailored(verified)
    pct, matched, missing = keyword_coverage([], tailored)
    assert pct is None
    assert matched == []
    assert missing == []


# --- audit verdict ---


def test_audit_ship(verified: dict) -> None:
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=_score(),
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
    )
    assert result.verdict == "ship"
    assert result.fabrication_flags == []


def test_audit_revise_on_low_coverage(verified: dict) -> None:
    score_missing = _score(must_haves=["Angular", "Vue", "Kubernetes", "Terraform", "Go"])
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=score_missing,
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
    )
    assert result.verdict == "revise"
    assert result.keyword_coverage_pct < 70


def test_audit_revise_on_cover_violation(verified: dict) -> None:
    bad_cover = _good_cover()
    bad_cover.body[0] = "I am passionate about this role at Acme Corp and TypeScript."
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=bad_cover,
        score=_score(),
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
    )
    assert result.verdict == "revise"
    assert result.cover_letter_violations


def test_audit_falls_back_to_jd_when_score_must_haves_empty(verified: dict) -> None:
    """When the score LLM returns empty matched_must_haves (qwen3.5:9b often
    does this even though the schema requires it), the audit must extract
    must-haves deterministically from the JD by intersecting verified skills
    with the JD text — otherwise the 70% coverage gate is silently bypassed.
    """
    empty_score = ScoreResult(
        score=85,
        matched_must_haves=[],
        gaps=[],
        decline_reason=None,
        ai_bonus_present=False,
        model="test",
    )
    jd = "We need a TypeScript and React developer with Shopify experience."
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=empty_score,
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
        job_description=jd,
    )
    assert result.keyword_coverage_pct is not None
    assert "TypeScript" in result.matched_keywords
    assert "React" in result.matched_keywords


def test_audit_block_on_fabrication(verified: dict) -> None:
    tailored = _minimal_tailored(verified)
    tailored.roles.append(
        TailoredRole(title="Engineer", employer="Fake Corp", dates="2025", bullets=["Did stuff."])
    )
    result = audit(
        tailored=tailored,
        cover=_good_cover(),
        score=_score(),
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
    )
    assert result.verdict == "block"
    assert result.fabrication_flags
