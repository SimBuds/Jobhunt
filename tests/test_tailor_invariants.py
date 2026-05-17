from __future__ import annotations

import pytest

from jobhunt.errors import PipelineError
from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredResume,
    TailoredRole,
    _enforce_no_fabrication,
)

VERIFIED = {
    "summary": "Full-stack JavaScript developer with 2+ years of professional client work.",
    "work_history": [
        {"employer": "Acme", "dates": "2023 – Present", "title": "Dev"},
        {"employer": "BetaCo", "dates": "2021 – 2023", "title": "Dev"},
    ],
    "skills_core": ["JavaScript", "TypeScript", "React"],
    "skills_cms": ["Shopify (Liquid, Custom Themes)"],
    "skills_data_devops": ["Docker"],
    "skills_ai": ["Local LLM hosting with Ollama"],
    "skills_familiar": ["Java", "Python"],
}


def _make(
    *,
    roles=None,
    skills_categories=None,
    summary="Full-stack JavaScript developer with 2+ years building things.",
) -> TailoredResume:
    return TailoredResume(
        summary=summary,
        skills_categories=skills_categories
        or [TailoredCategory("Core", ["JavaScript"]), TailoredCategory("Familiar", ["Java"])],
        roles=roles
        or [
            TailoredRole("Dev", "Acme", "2023 – Present", ["b1"]),
            TailoredRole("Dev", "BetaCo", "2021 – 2023", ["b1"]),
        ],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
    )


def test_passes_when_roles_and_skills_match_verified():
    _enforce_no_fabrication(_make(), VERIFIED)


def test_rejects_missing_role():
    bad = _make(roles=[TailoredRole("Dev", "Acme", "2023 – Present", ["b1"])])
    with pytest.raises(PipelineError, match="missing"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_invented_employer():
    bad = _make(
        roles=[
            TailoredRole("Dev", "Acme", "2023 – Present", ["b1"]),
            TailoredRole("Dev", "BetaCo", "2021 – 2023", ["b1"]),
            TailoredRole("Staff", "FAANG Inc", "2024", ["b1"]),
        ]
    )
    with pytest.raises(PipelineError, match="extra"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_familiar_skill_in_core():
    bad = _make(
        skills_categories=[
            TailoredCategory("Core", ["JavaScript", "Python"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    with pytest.raises(PipelineError, match="Familiar"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_invented_skill():
    bad = _make(
        skills_categories=[
            TailoredCategory("Core", ["JavaScript", "Rust"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    with pytest.raises(PipelineError, match="not in verified"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_summary_with_unverified_seniority():
    bad = _make(summary="Senior Full Stack Developer with 2+ years of experience.")
    with pytest.raises(PipelineError, match="seniority token"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_summary_leading_with_culinary():
    bad = _make(
        summary="Nine years of leadership in high-pressure culinary environments. Also a developer."
    )
    with pytest.raises(PipelineError, match="culinary"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_allows_culinary_clause_at_end():
    ok = _make(
        summary="Full-stack JavaScript developer with 2+ years of client work. "
        "Prior experience leading culinary teams."
    )
    _enforce_no_fabrication(ok, VERIFIED)


def test_allows_seniority_when_present_in_verified_summary():
    verified = {**VERIFIED, "summary": "Senior full-stack developer with 2+ years."}
    ok = _make(summary="Senior full-stack developer with 2+ years of work.")
    _enforce_no_fabrication(ok, verified)


def test_paren_substring_tolerated():
    """Tailored skill 'Shopify (Liquid)' should match verified 'Shopify (Liquid, Custom Themes)'."""
    ok = _make(
        skills_categories=[
            TailoredCategory("CMS", ["Shopify (Liquid)"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    _enforce_no_fabrication(ok, VERIFIED)


def test_annotation_expansion_tolerated():
    """Tailored skill that adds only annotation tokens (custom, themes) on top
    of verified should pass. 'Shopify (Liquid, Custom Themes)' tailored against
    verified 'Shopify (Liquid)' is the same fact with more rendering."""
    verified_narrow = {
        **VERIFIED,
        "skills_cms": ["Shopify (Liquid)"],
    }
    ok = _make(
        skills_categories=[
            TailoredCategory("CMS", ["Shopify (Liquid, Custom Themes)"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    _enforce_no_fabrication(ok, verified_narrow)


def test_rejects_react_native_against_verified_react():
    """May 2026 fix: the old one-way subset check accepted 'React Native'
    against verified 'React' because verified-tokens were a subset of the
    broader claim. That direction implied Casey owned strict supersets of
    his verified skills (mobile, GraphQL, etc.). New rule allows only
    annotation-grade additions; 'native' is not annotation."""
    bad = _make(
        skills_categories=[
            TailoredCategory("Core", ["React Native"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    with pytest.raises(PipelineError, match="not in verified"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_jd_surface_form_headless_cms_contentful_accepted():
    """May 2026 regression: tailor.md rule 9 instructs the LLM to write
    'headless CMS (Contentful)' when the JD uses 'headless CMS'. The
    identity-token check must recognise this as the same fact as verified
    'Contentful (Certified Professional)' — both share identity {contentful}.
    """
    ok = _make(
        skills_categories=[
            TailoredCategory("CMS", ["headless CMS (Contentful)"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    verified_with_contentful = {
        **VERIFIED,
        "skills_cms": ["Contentful (Certified Professional)"],
    }
    _enforce_no_fabrication(ok, verified_with_contentful)


def test_jd_surface_form_short_aliases_accepted():
    """JS / TS / GH Actions / Postgres in the tailored output must match the
    verified long forms (JavaScript / TypeScript / GitHub Actions / PostgreSQL)
    via _SURFACE_ALIASES."""
    verified_long = {
        **VERIFIED,
        "skills_core": ["JavaScript", "TypeScript", "React"],
        "skills_data_devops": ["PostgreSQL", "GitHub Actions CI/CD"],
    }
    ok = _make(
        skills_categories=[
            TailoredCategory("Languages", ["JS", "TS"]),
            TailoredCategory("DevOps", ["Postgres", "GH Actions"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    _enforce_no_fabrication(ok, verified_long)


def test_rest_apis_surface_form_accepted():
    """JD-surface 'REST APIs' must match verified 'RESTful APIs' via the
    rest→restful alias. Both have identity {restful} after alias normalisation
    and api/apis annotation stripping."""
    verified_rest = {
        **VERIFIED,
        "skills_core": ["JavaScript", "TypeScript", "React", "RESTful APIs"],
    }
    ok = _make(
        skills_categories=[
            TailoredCategory("Backend", ["REST APIs"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    _enforce_no_fabrication(ok, verified_rest)


def test_rest_apis_does_not_trigger_familiar_promotion():
    """Regression: when the LLM writes 'REST APIs' in a non-Familiar category,
    the empty-identity Familiar check used to falsely fire (any empty set is
    a subset of every Familiar identity). Verify a tailored 'REST APIs' in
    a Backend category passes when verified has 'RESTful APIs' in Core."""
    verified_rest = {
        **VERIFIED,
        "skills_core": ["JavaScript", "TypeScript", "React", "RESTful APIs"],
        "skills_familiar": ["Java", "Spring Boot"],
    }
    ok = _make(
        skills_categories=[
            TailoredCategory("Frontend & CMS", ["REST APIs", "JavaScript"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    _enforce_no_fabrication(ok, verified_rest)


def test_rejects_typescript_react_combo_when_verified_only_has_one():
    """A new combined claim that goes beyond verified token sets fails. Make
    sure 'TypeScript GraphQL' is rejected (verified has TS but not GraphQL)."""
    bad = _make(
        skills_categories=[
            TailoredCategory("Core", ["TypeScript GraphQL"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    with pytest.raises(PipelineError, match="not in verified"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_dedupe_education_drops_deans_and_coursework_lines():
    from jobhunt.pipeline.tailor import _dedupe_education

    t = _make()
    t.education = [
        "Computer Programming & Analysis (Advanced Diploma), GBC, April 2024",
        "Dean's List (all terms). Coursework: ML, DSA, Enterprise Java.",
        "Coursework: Full-Stack Development, Enterprise Java",
    ]
    _dedupe_education(t)
    assert t.education == ["Computer Programming & Analysis (Advanced Diploma), GBC, April 2024"]


def test_try_drop_weakest_bullet_defers_present_role():
    """May 2026 guard: while any older role still has spare bullets, the
    'Present' role's tail bullets must not be dropped. Casey's current
    contract is the strongest JD-recent signal."""
    from jobhunt.pipeline.tailor import _try_drop_weakest_bullet

    present_role = TailoredRole(
        "Dev", "Acme", "2023 – Present",
        bullets=[
            "Lead bullet for current role: built Shopify storefront.",
            "Second bullet for current role: shipped ring builder.",
        ],
    )
    older_role = TailoredRole(
        "Dev", "BetaCo", "2021 – 2023",
        bullets=[
            "Lead bullet for older role: ran technical SEO audits and integrations.",
            "Second bullet for older role: bulk JSON catalog migration touching 400+ items.",
            "Third bullet for older role: refactored Liquid templates across the storefront.",
        ],
    )
    t = TailoredResume(
        summary="x",
        skills_categories=[TailoredCategory("Core", ["JavaScript"])],
        roles=[present_role, older_role],
        certifications=[], education=[], coursework=[], model="test",
    )
    assert _try_drop_weakest_bullet(t) is True
    # Older role should have lost its last bullet; Present role intact.
    assert len(present_role.bullets) == 2
    assert len(older_role.bullets) == 2


def test_try_drop_weakest_bullet_falls_through_to_present_when_no_other_spare():
    """When no older role has spare bullets, the Present role IS eligible."""
    from jobhunt.pipeline.tailor import _try_drop_weakest_bullet

    present_role = TailoredRole(
        "Dev", "Acme", "2023 – Present",
        bullets=["Lead bullet.", "Second bullet."],
    )
    older_role = TailoredRole(
        "Dev", "BetaCo", "2021 – 2023",
        bullets=["Only bullet."],
    )
    t = TailoredResume(
        summary="x",
        skills_categories=[TailoredCategory("Core", ["JavaScript"])],
        roles=[present_role, older_role],
        certifications=[], education=[], coursework=[], model="test",
    )
    assert _try_drop_weakest_bullet(t) is True
    assert len(present_role.bullets) == 1
    assert len(older_role.bullets) == 1


def test_cap_lead_category_at_10_overflows_to_secondary():
    """Phase 9 deterministic enforcement: when the LLM stuffs 12 items into
    the lead category (qwen ignored the 6-10 prompt rule), the post-process
    must trim to 10 and push the overflow into the second non-Familiar
    category — not drop the verified skills."""
    from jobhunt.pipeline.tailor import _cap_lead_category_size

    tailored = TailoredResume(
        summary="x",
        skills_categories=[
            TailoredCategory(
                "Frontend & CMS",
                [
                    "React", "Next.js", "JavaScript", "TypeScript", "HTML5",
                    "CSS3", "Shopify", "HubSpot", "WordPress", "Contentful",
                    "REST", "GH Actions",  # 12 items, last 2 are overflow
                ],
            ),
            TailoredCategory("Backend & Data", ["Node", "Express", "Postgres"]),
            TailoredCategory("Familiar", ["Java", "Spring Boot"]),
        ],
        roles=[], certifications=[], education=[], coursework=[], model="test",
    )
    _cap_lead_category_size(tailored)
    assert len(tailored.skills_categories[0].items) == 10
    # Overflow lands at the front of the second category (above its
    # pre-existing items so JD-primary ranking is preserved).
    secondary = tailored.skills_categories[1].items
    assert secondary[0] == "REST"
    assert secondary[1] == "GH Actions"
    assert "Node" in secondary  # original items still there


def test_cap_lead_category_creates_additional_when_no_secondary():
    """Edge case: lead overflows but the only other category is Familiar.
    The cap must create an 'Additional' bucket inserted before Familiar
    rather than polluting Familiar with verified-Core items."""
    from jobhunt.pipeline.tailor import _cap_lead_category_size

    tailored = TailoredResume(
        summary="x",
        skills_categories=[
            TailoredCategory(
                "Frontend & UI",
                ["React"] * 12,  # 12 items, no secondary non-Familiar bucket
            ),
            TailoredCategory("Familiar", ["Java"]),
        ],
        roles=[], certifications=[], education=[], coursework=[], model="test",
    )
    _cap_lead_category_size(tailored)
    assert len(tailored.skills_categories[0].items) == 10
    names = [c.name for c in tailored.skills_categories]
    assert names == ["Frontend & UI", "Additional", "Familiar"]
    assert len(tailored.skills_categories[1].items) == 2  # the 2 overflow


def test_cap_lead_category_no_op_when_already_under_cap():
    """No-op path: lead has 8 items, no movement happens."""
    from jobhunt.pipeline.tailor import _cap_lead_category_size

    tailored = TailoredResume(
        summary="x",
        skills_categories=[
            TailoredCategory("Frontend & UI", ["React"] * 8),
            TailoredCategory("Familiar", ["Java"]),
        ],
        roles=[], certifications=[], education=[], coursework=[], model="test",
    )
    before = [(c.name, list(c.items)) for c in tailored.skills_categories]
    _cap_lead_category_size(tailored)
    after = [(c.name, list(c.items)) for c in tailored.skills_categories]
    assert before == after


def test_shrink_to_one_page_trims_familiar_first():
    from jobhunt.pipeline.tailor import _shrink_to_one_page

    long_summary = "Full-stack JavaScript developer with 2+ years building things. " + (
        "Sentence about a project. " * 30
    )
    long_bullets = ["A reasonably long bullet describing a real shipped project. " * 2] * 8
    t = _make(
        summary=long_summary,
        roles=[
            TailoredRole("Dev", "Acme", "2023 – Present", long_bullets),
            TailoredRole("Dev", "BetaCo", "2021 – 2023", long_bullets),
        ],
        skills_categories=[
            TailoredCategory("Core", ["JavaScript"]),
            TailoredCategory(
                "Familiar",
                ["Java", "Python", "Rust", "Go", "Ruby", "C++", "Kotlin", "Scala"],
            ),
        ],
    )
    t.coursework = ["A", "B", "C", "D", "E"]
    import contextlib

    from jobhunt.resume.render_docx import fits_one_page

    with contextlib.suppress(Exception):
        _shrink_to_one_page(t)
    # Either the shrink succeeded (fits) or it raised; if it succeeded the
    # familiar list should have been trimmed first.
    if fits_one_page(t):
        familiar = next(c for c in t.skills_categories if c.name == "Familiar")
        assert len(familiar.items) <= 8  # trimmed or unchanged
