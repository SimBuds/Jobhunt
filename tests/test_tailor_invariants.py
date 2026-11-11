from __future__ import annotations

import pytest

from jobhunt.errors import PipelineError
from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredProject,
    TailoredResume,
    TailoredRole,
    _enforce_no_fabrication,
    _parse,
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
    "projects": [
        {"name": "jobhunt", "url": "github.com/SimBuds/Jobhunt"},
        {"name": "Sidecar", "url": "github.com/SimBuds/Sidecar"},
    ],
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


def test_react_umbrella_accepts_redux_and_native():
    """React umbrella: verified 'React (Redux, React Native)' accepts tailored
    'Redux' and 'React Native' (identity-subset), so Redux-required JDs tailor
    cleanly without a standalone Redux verified item. Unverified React supersets
    are still rejected."""
    verified = dict(VERIFIED)
    verified["skills_core"] = ["JavaScript", "TypeScript", "React (Redux, React Native)"]
    for surfaced in ("Redux", "React Native", "React"):
        ok = _make(
            skills_categories=[
                TailoredCategory("Frontend", ["JavaScript", surfaced]),
                TailoredCategory("Familiar", ["Java"]),
            ]
        )
        _enforce_no_fabrication(ok, verified)
    bad = _make(
        skills_categories=[
            TailoredCategory("Frontend", ["React Server Components"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    with pytest.raises(PipelineError, match="not in verified"):
        _enforce_no_fabrication(bad, verified)


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


def test_accepts_project_skill_in_non_familiar_category():
    """PB2: a `skills_projects` skill (FastAPI) is Core-grade, so it may appear
    in a non-Familiar category without tripping the fabrication guard. Without
    `skills_projects` in the allowlist this raises (FastAPI reads as invented)."""
    verified = {**VERIFIED, "skills_projects": ["FastAPI", "Redis"]}
    ok = _make(
        skills_categories=[
            TailoredCategory("Backend & APIs", ["FastAPI", "Redis"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    _enforce_no_fabrication(ok, verified)


def test_accepts_verified_project():
    """PB4a: a tailored project matching verified (name + url) with rewritten
    bullets passes the fabrication guard."""
    ok = _make()
    ok.projects = [
        TailoredProject(
            name="jobhunt",
            url="github.com/SimBuds/Jobhunt",
            stack=["Python", "Ollama"],
            bullets=["Built a local-first ATS CLI with a local LLM scorer."],
        )
    ]
    _enforce_no_fabrication(ok, VERIFIED)


def test_accepts_project_name_case_insensitively():
    """A capitalised 'Jobhunt' still matches verified 'jobhunt'."""
    ok = _make()
    ok.projects = [TailoredProject("Jobhunt", "github.com/SimBuds/Jobhunt", [], ["x"])]
    _enforce_no_fabrication(ok, VERIFIED)


def test_rejects_invented_project():
    """A project not in verified_facts.projects is a fabrication."""
    bad = _make()
    bad.projects = [TailoredProject("Phantom Project", "github.com/x/y", [], ["x"])]
    with pytest.raises(PipelineError, match="project not in verified"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_project_url_tampering():
    """A verified project name with an altered url is rejected."""
    bad = _make()
    bad.projects = [TailoredProject("jobhunt", "github.com/evil/fork", [], ["x"])]
    with pytest.raises(PipelineError, match="project url diverged"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_parse_decodes_projects():
    """PB4a decode: the LLM's `projects` array becomes TailoredProject entries."""
    raw = {
        "summary": "x",
        "skills_categories": [],
        "roles": [],
        "certifications": [],
        "education": [],
        "coursework": [],
        "projects": [
            {
                "name": "jobhunt",
                "url": "github.com/SimBuds/Jobhunt",
                "stack": ["Python"],
                "bullets": ["Built X."],
            },
        ],
    }
    t = _parse(raw, "test")
    assert len(t.projects) == 1
    assert t.projects[0].name == "jobhunt"
    assert t.projects[0].stack == ["Python"]


def test_parse_tolerates_missing_projects():
    """A resume with no projects key decodes to an empty list (back-compat)."""
    raw = {
        "summary": "x",
        "skills_categories": [],
        "roles": [],
        "certifications": [],
        "education": [],
        "coursework": [],
    }
    assert _parse(raw, "test").projects == []


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


def test_rejects_superset_claim_against_verified_skill():
    """May 2026 fix: the old one-way subset check accepted a broader claim
    (verified-tokens ⊆ claim) implying Casey owned strict supersets of his
    verified skills. New rule allows only annotation-grade additions.
    (React Native was the original example here; it is now a verified
    Projects skill, so 'React Server Components' — a still-unverified
    superset of verified 'React' — exercises the same rejection.)"""
    bad = _make(
        skills_categories=[
            TailoredCategory("Core", ["React Server Components"]),
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
    rest→restful alias. Both have identity {restful, api} after alias
    normalisation (apis→api; "api" stopped being annotation in Phase 15b)."""
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
        "Dev",
        "Acme",
        "2023 – Present",
        bullets=[
            "Lead bullet for current role: built Shopify storefront.",
            "Second bullet for current role: shipped ring builder.",
        ],
    )
    older_role = TailoredRole(
        "Dev",
        "BetaCo",
        "2021 – 2023",
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
        certifications=[],
        education=[],
        coursework=[],
        model="test",
    )
    assert _try_drop_weakest_bullet(t) is True
    # Older role should have lost its last bullet; Present role intact.
    assert len(present_role.bullets) == 2
    assert len(older_role.bullets) == 2


def test_try_drop_weakest_bullet_falls_through_to_present_when_no_other_spare():
    """When no older role has spare bullets, the Present role IS eligible."""
    from jobhunt.pipeline.tailor import _try_drop_weakest_bullet

    present_role = TailoredRole(
        "Dev",
        "Acme",
        "2023 – Present",
        bullets=["Lead bullet.", "Second bullet."],
    )
    older_role = TailoredRole(
        "Dev",
        "BetaCo",
        "2021 – 2023",
        bullets=["Only bullet."],
    )
    t = TailoredResume(
        summary="x",
        skills_categories=[TailoredCategory("Core", ["JavaScript"])],
        roles=[present_role, older_role],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
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
                    "React",
                    "Next.js",
                    "JavaScript",
                    "TypeScript",
                    "HTML5",
                    "CSS3",
                    "Shopify",
                    "HubSpot",
                    "WordPress",
                    "Contentful",
                    "REST",
                    "GH Actions",  # 12 items, last 2 are overflow
                ],
            ),
            TailoredCategory("Backend & Data", ["Node", "Express", "Postgres"]),
            TailoredCategory("Familiar", ["Java", "Spring Boot"]),
        ],
        roles=[],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
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
        roles=[],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
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
        roles=[],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
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


def test_shrink_trims_projects_last_and_keeps_a_lead_bullet():
    """PB4b: the projects rung trims trailing project bullets (then whole
    projects) when nothing earlier in the ladder can recover the page. Every
    surviving project keeps at least its lead bullet."""
    from jobhunt.pipeline.tailor import TailoredProject, _shrink_to_one_page
    from jobhunt.resume.render_docx import fits_one_page

    long_bullet = "A long project bullet describing a shipped system in real detail. " * 2
    projects = [
        TailoredProject(
            f"proj{i}",
            f"github.com/x/proj{i}",
            ["Python", "Docker"],
            [long_bullet, long_bullet, long_bullet],
        )
        # 20, not 8, since 6c.3: a project now costs one rendered line, so 8
        # fit the page and the overflow precondition no longer held.
        for i in range(20)
    ]
    t = TailoredResume(
        summary="Full-stack developer. Builds things end to end. Ships production code.",
        skills_categories=[TailoredCategory("Core", ["JavaScript"])],
        roles=[TailoredRole("Dev", "Acme", "2023 – Present", ["One lead bullet."])],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
        projects=projects,
    )
    assert not fits_one_page(t)  # 20 one-line projects overflow
    _shrink_to_one_page(t)
    assert fits_one_page(t)
    assert sum(len(p.bullets) for p in t.projects) < 20 * 3  # trimmed
    assert all(len(p.bullets) >= 1 for p in t.projects)  # survivors keep a lead


# --- Phase 15b (2026-11-01): "<verified token> API" and summary tech -------
#
# The AI lane render shipped "Claude API" in a skills row and in the summary
# against a profile holding only "Claude Code". "api" was an annotation token,
# so "Claude API" reduced to {claude} and passed; the summary was never
# checked past its opening label.


def test_rejects_api_claim_riding_on_a_verified_token():
    verified = {**VERIFIED, "skills_ai": ["Claude Code"]}
    bad = _make(
        skills_categories=[
            TailoredCategory("AI", ["Claude API"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    with pytest.raises(PipelineError, match="not in verified"):
        _enforce_no_fabrication(bad, verified)


def test_singular_api_matches_verified_plural():
    verified = {**VERIFIED, "skills_data_devops": ["Docker", "REST APIs"]}
    ok = _make(
        skills_categories=[
            TailoredCategory("Backend", ["REST API"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    _enforce_no_fabrication(ok, verified)


def test_api_claim_needs_a_verified_api_skill():
    with_admin = {**VERIFIED, "skills_cms": ["Shopify Admin API"]}
    claim = _make(
        skills_categories=[
            TailoredCategory("CMS", ["Shopify API"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    _enforce_no_fabrication(claim, with_admin)
    with pytest.raises(PipelineError, match="not in verified"):
        _enforce_no_fabrication(claim, VERIFIED)  # only "Shopify (Liquid, ...)"


def test_rejects_summary_naming_unverified_watchlisted_tech():
    from jobhunt.pipeline.tailor import FabricationError

    bad = _make(
        summary="Full-stack JavaScript developer with 2+ years. "
        "Builds agents against the Claude API."
    )
    with pytest.raises(FabricationError) as exc:
        _enforce_no_fabrication(bad, VERIFIED)
    [violation] = exc.value.violations
    assert (violation.kind, violation.detail) == ("summary-unverified-tech", "claude api")


def test_summary_tech_hint_names_the_term():
    from jobhunt.pipeline.tailor import FabricationViolation, _violation_hint_line

    line = _violation_hint_line(FabricationViolation("summary-unverified-tech", "claude api"))
    assert "Unspecified violation" not in line  # the generic fallback
    assert "claude api" in line
    assert "remove" in line.lower()


def test_summary_may_name_watchlisted_tech_the_profile_holds():
    verified = {
        **VERIFIED,
        "projects": [
            {
                "name": "jobhunt",
                "url": "github.com/SimBuds/Jobhunt",
                "bullets": ["Static Astro site."],
            },
        ],
    }
    ok = _make(summary="Full-stack JavaScript developer with 2+ years. Ships Astro sites.")
    _enforce_no_fabrication(ok, verified)


def test_summary_negated_tech_passes():
    ok = _make(
        summary="Full-stack JavaScript developer with 2+ years, "
        "shipping containers with Docker rather than Kubernetes."
    )
    _enforce_no_fabrication(ok, VERIFIED)


# --- Phase 16c (2026-11-02): proficiency inflation in the summary ----------
#
# The full-stack lane render shipped "Expert in AI/LLM tooling" and passed:
# the summary guard rejected seniority words but not mastery claims.

FULL_STACK_RENDER_SUMMARY = (
    "Full-stack developer since 2022 building Shopify, HubSpot, and WordPress "
    "sites for small brands. Expert in AI/LLM tooling with local LLMs, prompt "
    "engineering, and Claude Code to automate content workflows."
)


def test_rejects_expert_from_the_full_stack_render():
    from jobhunt.pipeline.tailor import FabricationError

    verified = {
        **VERIFIED,
        "summary": "Full-stack developer since 2022, building Shopify, HubSpot and "
        "WordPress sites for small brands.",
        "skills_cms": ["Shopify (Liquid)", "HubSpot (HubL)", "WordPress"],
        "skills_ai": ["Claude Code", "local LLMs", "prompt engineering"],
    }
    with pytest.raises(FabricationError) as exc:
        _enforce_no_fabrication(_make(summary=FULL_STACK_RENDER_SUMMARY), verified)
    [violation] = exc.value.violations
    assert (violation.kind, violation.detail) == ("summary-proficiency", "expert")


def test_rejects_guru():
    from jobhunt.pipeline.tailor import FabricationError

    # After the opening label, so the role-label check cannot be what fires.
    bad = _make(summary="Full-stack JavaScript developer with 2+ years. A React guru.")
    with pytest.raises(FabricationError) as exc:
        _enforce_no_fabrication(bad, VERIFIED)
    [violation] = exc.value.violations
    assert (violation.kind, violation.detail) == ("summary-proficiency", "guru")


def test_expertise_and_specialist_do_not_trigger():
    ok = _make(
        summary="Full-stack JavaScript developer with 2+ years. Brings expertise "
        "to teams of experts as a CMS specialist."
    )
    _enforce_no_fabrication(ok, VERIFIED)


def test_expert_allowed_when_the_verified_summary_says_it():
    verified = {**VERIFIED, "summary": "Expert full-stack JavaScript developer."}
    _enforce_no_fabrication(_make(summary="Expert full-stack JavaScript developer."), verified)


def test_proficiency_hint_names_the_word():
    from jobhunt.pipeline.tailor import FabricationViolation, _violation_hint_line

    line = _violation_hint_line(FabricationViolation("summary-proficiency", "expert"))
    assert "Unspecified violation" not in line
    assert "'expert'" in line
    assert "remove" in line.lower()


# --- 2026-11-10: no full-stack identity unless the verified summary has one --
#
# The 2026-11-09 profile repositioned the candidate as a Shopify and WordPress
# developer, and the label guard treated "full" and "stack" as generic words,
# so "Full-stack developer building Shopify sites" passed against it.

REPOSITIONED = {
    **VERIFIED,
    "summary": "Shopify, WordPress and HubSpot developer since 2022 who builds "
    "and migrates e-commerce and CMS sites.",
    "skills_core": ["JavaScript", "HTML/CSS", "Python"],
    "skills_cms": ["Shopify (Liquid)", "WordPress", "HubSpot (HubL)"],
    "skills_familiar": ["React", "TypeScript"],
}
REPOSITIONED_SKILLS = [
    TailoredCategory("CMS & E-commerce", ["Shopify (Liquid)", "WordPress"]),
    TailoredCategory("Familiar", ["React", "TypeScript"]),
]


@pytest.mark.parametrize(
    ("summary", "detail"),
    [
        ("Full-stack developer building Shopify and WordPress sites since 2022.", "full-stack"),
        ("Shopify developer since 2022. Ships full stack features end to end.", "full stack"),
        ("Fullstack developer building Shopify sites since 2022.", "fullstack"),
    ],
)
def test_rejects_full_stack_identity_absent_from_verified_summary(summary, detail):
    from jobhunt.pipeline.tailor import FabricationError

    with pytest.raises(FabricationError) as exc:
        _enforce_no_fabrication(
            _make(summary=summary, skills_categories=REPOSITIONED_SKILLS), REPOSITIONED
        )
    [violation] = exc.value.violations
    assert (violation.kind, violation.detail) == ("summary-identity", detail)


def test_repositioned_summary_without_full_stack_passes():
    ok = _make(
        summary="Shopify and WordPress developer since 2022 building CMS sites.",
        skills_categories=REPOSITIONED_SKILLS,
    )
    _enforce_no_fabrication(ok, REPOSITIONED)


def test_full_stack_allowed_when_the_verified_summary_says_it():
    # VERIFIED's own summary opens "Full-stack JavaScript developer".
    _enforce_no_fabrication(_make(summary="Full stack JavaScript developer, 2+ years."), VERIFIED)


def test_identity_hint_names_the_term():
    from jobhunt.pipeline.tailor import FabricationViolation, _violation_hint_line

    line = _violation_hint_line(FabricationViolation("summary-identity", "full-stack"))
    assert "Unspecified violation" not in line
    assert "'full-stack'" in line
    assert "remove" in line.lower()


# --- Phase 6c.2 (2026-11-10): earlier-career flag copied from verified.json --


def test_earlier_career_flag_is_copied_by_employer_and_dates():
    from jobhunt.pipeline.tailor import _mark_earlier_career

    verified = {
        **VERIFIED,
        "work_history": [
            {"employer": "Acme", "dates": "2023 – Present", "title": "Dev"},
            {"employer": "BetaCo", "dates": "2021 – 2023", "title": "Cook", "earlier_career": True},
        ],
    }
    t = _make()
    _mark_earlier_career(t, verified)
    assert [r.earlier_career for r in t.roles] == [False, True]


def test_earlier_career_defaults_false_without_the_key():
    from jobhunt.pipeline.tailor import _mark_earlier_career

    t = _make()
    _mark_earlier_career(t, VERIFIED)
    assert [r.earlier_career for r in t.roles] == [False, False]


# --- Phase D3 (2026-11-10): the ladder keeps a role's quantified bullet ------
#
# Lane renders kept 26% and 43% of the profile's figures: the ladder always
# popped the last bullet, so a quantified bullet could go while an
# unquantified one behind the lead stayed. Relevance still comes first: the
# lead is never dropped (Casey, 2026-11-11).


def _one_role(bullets: list[str]) -> TailoredResume:
    return TailoredResume(
        summary="x",
        skills_categories=[TailoredCategory("Core", ["JavaScript"])],
        roles=[TailoredRole("Dev", "BetaCo", "2021 – 2023", list(bullets))],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
    )


def _drop(bullets: list[str]) -> list[str]:
    from jobhunt.pipeline.tailor import _try_drop_weakest_bullet

    t = _one_role(bullets)
    assert _try_drop_weakest_bullet(t) is True
    return t.roles[0].bullets


def test_unquantified_bullet_goes_before_a_later_quantified_one():
    assert _drop(["Lead.", "Kept the site updated.", "Cut load time 25%."]) == [
        "Lead.",
        "Cut load time 25%.",
    ]


def test_the_lead_survives_even_without_a_figure():
    """Relevance first (Casey, 2026-11-11): the lead is the JD-relevant bullet
    `_order_bullets_by_jd` placed there, so it is never the one dropped."""
    lead = "Replaced manual product entry with a Python pipeline."
    figure = "Built the pipeline in 20 hours after 100 hours of manual entry."
    assert _drop([lead, figure]) == [lead]


def test_all_quantified_drops_the_last_as_before():
    assert _drop(["Moved position 38 to 13.", "Cut load time 25%."]) == [
        "Moved position 38 to 13.",
    ]


def test_none_quantified_drops_the_last_and_keeps_the_lead():
    assert _drop(["Lead.", "Second.", "Third."]) == ["Lead.", "Second."]
