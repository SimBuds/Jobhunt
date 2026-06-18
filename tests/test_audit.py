"""Tests for pipeline.audit — keyword coverage + verdict logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunt.pipeline.audit import (
    AuditResult,
    _derive_project_anchors,
    _extract_must_haves_from_jd,
    _find_project_anchor,
    audit,
    keyword_coverage,
)
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
                "bullets": ["Built 16+ page Shopify storefront on a customized Dawn 2.0 theme."],
            },
            {
                "employer": "AI Agency (NDA)",
                "dates": "2026 – Present",
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


# Per-role (title, first bullet) by index. Employer + dates are pulled from the
# active `verified` fixture so the audit's fabrication re-check always sees a
# matching (employer, dates) — robust to the date format (e.g. bare "2023 –
# Present" vs the parenthesized "(2023 – Present)" the 2026-06 docx uses).
_ROLE_CONTENT = [
    ("Web Developer (Contract)", ["Built Shopify storefront."]),
    ("Web Developer (Contract)", ["Built HubSpot theme."]),
    ("Web Developer (Contract)", ["Built Shopify layouts."]),
    ("Sous Chef & Team Lead", ["Led culinary teams."]),
]


def _minimal_tailored(verified: dict) -> TailoredResume:
    roles = []
    for i, r in enumerate(verified.get("work_history", [])):
        title, bullets = (
            _ROLE_CONTENT[i] if i < len(_ROLE_CONTENT) else (r.get("title", "Developer"), ["Did work."])
        )
        roles.append(
            TailoredRole(title=title, employer=r["employer"], dates=r["dates"], bullets=list(bullets))
        )
    return TailoredResume(
        summary="TypeScript and React developer with Shopify and Node.js experience.",
        skills_categories=[
            TailoredCategory("Languages", ["TypeScript", "React", "Node.js"]),
            TailoredCategory("DevOps", ["GitHub Actions CI/CD"]),
            TailoredCategory("CMS", ["Shopify (Liquid, Custom Themes)"]),
            TailoredCategory("Familiar", ["Python"]),
        ],
        roles=roles,
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
            "The centrepiece project is the 16+ page Shopify storefront I built and maintained for a custom jewellery client over 2+ years.",
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
    pct, matched, missing = keyword_coverage(["TypeScript", "Rust", "Kubernetes"], tailored)
    assert "TypeScript" in matched
    assert "Rust" in missing
    assert "Kubernetes" in missing
    assert pct is not None and pct < 50


def test_keyword_coverage_peer_family(verified: dict) -> None:
    # Tailor renders JD surface forms per tailor.md rule 9; audit must accept any
    # peer-family member. Resume has React; JD asking for Angular/Vue/Svelte
    # should resolve via the frontend_framework peer family.
    tailored = _minimal_tailored(verified)
    pct, matched, missing = keyword_coverage(["Angular", "Vue", "Svelte"], tailored)
    assert "Angular" in matched
    assert "Vue" in matched
    assert "Svelte" in matched
    assert missing == []


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


def test_audit_revise_on_borderline_coverage(verified: dict) -> None:
    # 3 of 5 must-haves matched (60%) — below soft 70 floor, above hard 50 floor → revise.
    score_borderline = _score(must_haves=["TypeScript", "React", "Node.js", "Terraform", "Scala"])
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=score_borderline,
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
    )
    assert result.verdict == "revise"
    assert result.keyword_coverage_pct is not None
    assert 50 <= result.keyword_coverage_pct < 70


def test_audit_block_on_below_hard_floor_coverage(verified: dict) -> None:
    # 1 of 5 must-haves matched (20%) — below the 50% hard floor → block.
    # Submitting at this coverage is invisible to the keyword screen.
    score_failing = _score(must_haves=["TypeScript", "Terraform", "Go", "Rust", "Kubernetes"])
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=score_failing,
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
    )
    assert result.verdict == "block"
    assert result.keyword_coverage_pct is not None
    assert result.keyword_coverage_pct < 50


def test_audit_block_on_zero_coverage(verified: dict) -> None:
    # 0 of 5 must-haves matched (0%) — the OCR/Tesseract/Airflow case from
    # the 2026-05-27 audit. Tailor can't fabricate, screen will toss it.
    score_zero = _score(must_haves=["Tesseract", "Airflow", "Temporal", "Scala", "Rust"])
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=score_zero,
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
    )
    assert result.verdict == "block"
    assert result.keyword_coverage_pct == 0


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
    # React is matched; its verified label is the umbrella "React (Redux,
    # React Native)" (audit lists the full verified skill form, as it does for
    # "Shopify (Liquid, Custom Themes)").
    assert any("React" in k for k in result.matched_keywords)


def test_extract_must_haves_includes_project_skill() -> None:
    """PB2: a `skills_projects` skill named in the JD surfaces as a deterministic
    must-have. `_verified_skills` now includes the project bucket, so audit's
    fallback intersect can match it. Without the PB2 change FastAPI is invisible
    to the audit and the coverage gate silently ignores it."""
    verified = {
        "skills_core": ["TypeScript"],
        "skills_projects": ["FastAPI", "Redis"],
        "skills_familiar": ["Java"],
    }
    must_haves = _extract_must_haves_from_jd(
        "Backend role using FastAPI and Postgres.",
        verified,
        job_title="Backend Developer",
    )
    assert any("FastAPI" in m for m in must_haves)


def test_audit_short_jd_uses_peer_families(verified: dict) -> None:
    """May 2026 audit fallback: when the score's matched_must_haves is empty
    AND the JD is short (< 800 chars), the audit broadens its must-have
    extraction through PEER_FAMILIES. A JD that names 'Vue' should surface
    'React' as an inferred must-have for Casey (React is verified)."""
    empty_score = ScoreResult(
        score=72, matched_must_haves=[], gaps=[],
        decline_reason=None, ai_bonus_present=False, model="test",
    )
    short_jd = "Frontend role: Vue, TypeScript, REST APIs."  # < 800 chars
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=empty_score,
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
        job_description=short_jd,
    )
    # React is a peer of Vue → should surface as matched even though "React"
    # is not literally in the JD.
    assert "React" in result.matched_keywords or any(
        "react" in m.lower() for m in result.matched_keywords
    )


def test_audit_peer_broadening_suppressed_when_sibling_already_matched(
    verified: dict,
) -> None:
    """Phase 10.1: Casey has both AWS and Azure verified. JD only names AWS.
    Old peer-broadening (Phase 5.2) added Azure as an inferred must-have via
    the cloud_provider family — but AWS already matched directly. The tailor
    didn't include Azure (JD doesn't ask), audit marked it missing, coverage
    dropped to 80%. Dedupe: when a family sibling is already directly matched,
    suppress the peer-broadened add."""
    empty_score = ScoreResult(
        score=82, matched_must_haves=[], gaps=[],
        decline_reason=None, ai_bonus_present=False, model="test",
    )
    short_jd = (
        "AWS Cloud Developer: design serverless apps using AWS Lambda, "
        "API Gateway, DynamoDB, and S3. Strong AWS background required."
    )
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=empty_score,
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
        job_description=short_jd,
    )
    # AWS should be surfaced as a must-have (in matched OR missing) — JD
    # mentions AWS directly. Whether it's matched or missing depends on
    # whether the minimal tailored fixture includes AWS in its skills.
    all_must_haves = [*result.matched_keywords, *result.missing_must_haves]
    assert any("aws" in m.lower() for m in all_must_haves), (
        f"AWS should surface as a must-have: matched={result.matched_keywords}, "
        f"missing={result.missing_must_haves}"
    )
    # Azure must NOT appear anywhere — peer-broadening was suppressed because
    # AWS (its cloud_provider sibling) already matched directly. Without the
    # Phase 10.1 dedupe, Azure would show up as a spurious missing must-have.
    assert not any("azure" in m.lower() for m in all_must_haves), (
        f"Azure spuriously surfaced via peer broadening: "
        f"matched={result.matched_keywords}, missing={result.missing_must_haves}"
    )


def test_audit_peer_broadening_still_fires_when_no_sibling_matched(
    verified: dict,
) -> None:
    """Sanity: the dedupe must not suppress the legitimate Vue→React inference.
    JD names Vue (no AWS/Azure equivalent overlap), verified has React but no
    Vue. Peer-broadening should still add React as an inferred must-have."""
    empty_score = ScoreResult(
        score=72, matched_must_haves=[], gaps=[],
        decline_reason=None, ai_bonus_present=False, model="test",
    )
    short_jd = "Frontend role: Vue, TypeScript, REST APIs."
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=empty_score,
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
        job_description=short_jd,
    )
    # React must still surface as matched (verified peer of JD-named Vue).
    assert any("react" in m.lower() for m in result.matched_keywords)


def test_audit_long_jd_does_not_use_peer_broadening(verified: dict) -> None:
    """Long JDs (>= 800 chars) skip the peer-family broadening — they have
    enough surface text to name canonical tech directly, and broadening would
    create false positives on roles that intentionally call out non-peers."""
    empty_score = ScoreResult(
        score=72, matched_must_haves=[], gaps=[],
        decline_reason=None, ai_bonus_present=False, model="test",
    )
    long_jd = "Senior frontend engineer needed. " + ("We use Vue 3 in production. " * 40)
    assert len(long_jd) >= 800
    result = audit(
        tailored=_minimal_tailored(verified),
        cover=_good_cover(),
        score=empty_score,
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
        job_description=long_jd,
    )
    # Without broadening, React should NOT surface as matched.
    assert not any(
        m.lower().strip() == "react" for m in result.matched_keywords
    )


def test_audit_alignment_flags_drift_between_resume_and_cover(verified: dict) -> None:
    """Cover middle paragraph anchors on Atelier Dacko (custom jewellery),
    but tailored resume's first role's first bullet anchors on HubSpot.
    The alignment check should flag a `revise` (not block)."""
    tailored = _minimal_tailored(verified)
    # Re-anchor lead bullet to HubSpot instead of Shopify.
    tailored.roles[0] = TailoredRole(
        title=tailored.roles[0].title,
        employer=tailored.roles[0].employer,
        dates=tailored.roles[0].dates,
        bullets=["Built a custom 8-page HubSpot theme with HubL modules."],
    )
    cover = _good_cover()
    # Cover middle paragraph names the Atelier Dacko ring builder (atelier
    # anchor). Uses verified-spelling terms now that anchors are derived from
    # verified.json rather than a hard-coded variant list.
    cover.body[1] = (
        "The centrepiece project is the Atelier Dacko ring builder, a 14+ "
        "page Shopify storefront I built over 2+ years."
    )
    cover.body[2] = "A second project: bulk JSON data migrations."  # no hubspot
    result = audit(
        tailored=tailored,
        cover=cover,
        score=_score(),
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
    )
    assert result.alignment_flags, result.alignment_flags
    assert result.verdict == "revise"


def test_audit_alignment_passes_when_both_anchor_on_same_project(
    verified: dict,
) -> None:
    """When the cover's middle paragraphs and resume lead bullet anchor on the
    same project (Atelier Dacko), no alignment flag fires."""
    tailored = _minimal_tailored(verified)
    tailored.roles[0] = TailoredRole(
        title=tailored.roles[0].title,
        employer=tailored.roles[0].employer,
        dates=tailored.roles[0].dates,
        bullets=[
            "Built the Atelier Dacko ring builder on Shopify with Stripe payments."
        ],
    )
    cover = _good_cover()
    cover.body[1] = (
        "Atelier Dacko's ring builder is the centerpiece — I designed the "
        "stone-band-size flow end to end."
    )
    result = audit(
        tailored=tailored,
        cover=cover,
        score=_score(),
        verified=verified,
        company="Acme Corp",
        cover_max_words=280,
    )
    assert result.alignment_flags == []


def test_derive_project_anchors_is_distinctive_and_data_driven() -> None:
    """Anchors are derived from verified.json, not hard-coded. Distinct sources
    get distinct keys, a shared platform (Shopify) is non-distinctive so it never
    anchors, and a term that identifies one source resolves to that source."""
    v = {
        "work_history": [
            {
                "employer": "Custom Jewelry Brand (Atelier Dacko)",
                "dates": "x",
                "bullets": ["Built a Shopify storefront with an interactive ring builder."],
            },
            {
                "employer": "Marketing Agency (Confidential)",
                "dates": "y",
                "bullets": ["Built a custom HubSpot theme with reusable HubL modules."],
            },
            {
                "employer": "Vintage Gaming Retailer (Confidential)",
                "dates": "z",
                "bullets": ["Built custom Shopify layouts for a vintage gaming catalog."],
            },
        ],
        "projects": [],
    }
    anchors = _derive_project_anchors(v)
    keys = {k for k, _ in anchors}
    assert "atelier_dacko" in keys  # key derived from the parenthetical name

    atelier = _find_project_anchor("I designed the ring builder flow", anchors)
    hubspot = _find_project_anchor("built a HubSpot theme with HubL", anchors)
    assert atelier and hubspot and atelier != hubspot

    # "Shopify" appears in two sources -> non-distinctive -> never an anchor term.
    assert _find_project_anchor("a generic Shopify build", anchors) is None


def test_audit_topics_categorisation(verified: dict) -> None:
    """The _audit_topics helper in apply_cmd produces coarse-grained labels
    for end-of-loop summarisation. Confirm each category lights up correctly
    so the summary histogram aggregates as expected.
    """
    from jobhunt.commands.apply_cmd import _audit_topics
    from jobhunt.pipeline.audit import AuditResult

    clean = AuditResult(
        keyword_coverage_pct=90, matched_keywords=["TypeScript"],
        missing_must_haves=[], fabrication_flags=[],
        cover_letter_violations=[], alignment_flags=[], verdict="ship",
    )
    assert _audit_topics(clean) == []

    low_coverage = AuditResult(
        keyword_coverage_pct=40, matched_keywords=["TypeScript"],
        missing_must_haves=["React", "GraphQL", "Vue"], fabrication_flags=[],
        cover_letter_violations=[], alignment_flags=[], verdict="revise",
    )
    assert _audit_topics(low_coverage) == ["coverage"]

    everything = AuditResult(
        keyword_coverage_pct=40, matched_keywords=[], missing_must_haves=["X"],
        fabrication_flags=["fake employer"],
        cover_letter_violations=["banned phrase"],
        alignment_flags=["drift"], verdict="block",
    )
    topics = _audit_topics(everything)
    assert set(topics) == {"fabrication", "cover-violation", "coverage", "alignment"}


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
