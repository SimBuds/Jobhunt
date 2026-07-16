"""Regression tests for `_keywords.phrase_present` normalization.

2026-07-16 Rippling/Future Buildings block: the score LLM emits must-have/gap
strings decorated with commentary — 'WordPress (exact match)', 'CSS3/Sass
(Core)' — and the audit fed them verbatim into `phrase_present`, which required
every non-stopword token to appear in the resume. Commentary tokens ('exact',
'match') and slash-fused tokens ('css3/sass', 'git/github') can never appear,
so four skills literally present on the tailored resume were counted missing,
dropping coverage to 46% (< 50% hard floor) and blocking a strong-fit job.

Fix: strip parenthetical qualifiers before matching, and treat '/'-compounds
as alternatives (token-level and whole-phrase-level).
"""

from __future__ import annotations

from jobhunt.pipeline._keywords import phrase_present
from jobhunt.pipeline.audit import keyword_coverage
from jobhunt.pipeline.tailor import TailoredCategory, TailoredResume, TailoredRole

# Mirrors the skills/summary the tailor actually produced for the blocked job
# (data/applications/manual_c136f53de7a2/tailor-diff.md), lower-cased the way
# `audit._resume_text` flattens it.
_RESUME_BLOB = "\n".join(
    [
        "full-stack javascript/typescript developer with 3+ years of cms & "
        "e-commerce client delivery across shopify, hubspot, and wordpress",
        "wordpress (elementor)",
        "shopify (liquid, custom themes)",
        "technical seo (on-page, core web vitals, pagespeed)",
        "react (redux, react native)",
        "next.js",
        "html5",
        "css3",
        "sass",
        "javascript (es6+)",
        "node.js",
        "mysql",
        "git",
        "github actions ci/cd",
        "aws",
        "rest apis",
    ]
)


# --- the four wrongly-missed phrases from the 2026-07-16 audit ---


def test_annotated_phrase_matches_despite_commentary() -> None:
    # 'exact'/'match' are LLM commentary, not keyword tokens.
    assert phrase_present("WordPress (exact match)", _RESUME_BLOB)


def test_slash_compound_matches_separately_listed_skills() -> None:
    # Resume lists CSS3 and Sass as separate skill items.
    assert phrase_present("CSS3/Sass (Core)", _RESUME_BLOB)


def test_slash_compound_with_multiword_tail_matches() -> None:
    assert phrase_present("Git/GitHub Actions CI/CD (Core)", _RESUME_BLOB)


def test_whole_phrase_slash_alternative_matches() -> None:
    # 'Performance Optimization' OR 'Core Web Vitals' — resume has the latter.
    assert phrase_present(
        "Performance Optimization/Core Web Vitals (Core project experience)",
        _RESUME_BLOB,
    )


# --- genuinely-missing phrases must stay missing ---


def test_absent_skill_still_missing_despite_annotation() -> None:
    assert not phrase_present(
        "Terraform (not in verified_facts; only coursework listed, no production usage)",
        _RESUME_BLOB,
    )


def test_absent_slash_compound_still_missing() -> None:
    assert not phrase_present("GraphQL/WPGraphQL (integration requirement)", _RESUME_BLOB)


def test_short_slash_fragments_do_not_split() -> None:
    # 'CI/CD' is one concept; 'ci'/'cd' fragments must not match substrings
    # like 'circleci' or 'cdn' on their own.
    assert not phrase_present("CI/CD", "we use circleci and a cdn")
    assert phrase_present("CI/CD", "github actions ci/cd pipelines")


def test_paren_only_phrase_falls_back_to_original() -> None:
    # When stripping leaves nothing, the original phrase's tokens still decide —
    # neither match-everything nor match-nothing.
    assert not phrase_present("(Kubernetes)", _RESUME_BLOB)
    assert phrase_present("(WordPress)", _RESUME_BLOB)


# --- end-to-end: the blocked job's exact must-have list now clears the floor ---


def _rippling_tailored() -> TailoredResume:
    return TailoredResume(
        summary=(
            "Full-stack JavaScript/TypeScript developer with 3+ years of CMS & "
            "e-commerce client delivery across Shopify, HubSpot, and WordPress."
        ),
        skills_categories=[
            TailoredCategory(
                "CMS & E-commerce",
                [
                    "WordPress (Elementor)",
                    "Shopify (Liquid, Custom Themes)",
                    "Technical SEO (on-page, Core Web Vitals, PageSpeed)",
                    "REST APIs",
                ],
            ),
            TailoredCategory("Frontend & UI", ["HTML5", "CSS3", "Sass", "JavaScript (ES6+)"]),
            TailoredCategory("Backend & Data", ["Node.js", "MySQL"]),
            TailoredCategory("DevOps & Infrastructure", ["Git", "GitHub Actions CI/CD", "AWS"]),
        ],
        roles=[
            TailoredRole(
                title="CMS / E-commerce Developer",
                employer="Atelier Dacko, Custom Jewelry Brand",
                dates="(Apr 2023 – Present)",
                bullets=["Migrated the brand's WordPress portfolio to Shopify."],
            )
        ],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
    )


def test_rippling_audit_coverage_clears_hard_floor() -> None:
    """The exact 13 must-have strings from the blocked audit.json. Ten are on
    the resume (46% -> 77%); PHP/Terraform/GraphQL stay honestly missing."""
    must_haves = [
        "HTML5 (Core)",
        "JavaScript/TypeScript (Core)",
        "MySQL (Core)",
        "RESTful APIs (Core)",
        "AWS (Core)",
        "SEO (Core skill)",
        "WordPress (exact match)",
        "PHP (implied via WordPress development context; core skill for WP roles)",
        "CSS3/Sass (Core)",
        "Git/GitHub Actions CI/CD (Core)",
        "Performance Optimization/Core Web Vitals (Core project experience)",
        "Terraform (not in verified_facts; only coursework listed, no production usage)",
        "GraphQL/WPGraphQL (JD lists as integration requirement)",
    ]
    pct, matched, missing = keyword_coverage(must_haves, _rippling_tailored())
    assert pct == 77, f"expected 10/13=77%, got {pct} (missing={missing})"
    assert set(missing) == {
        "PHP (implied via WordPress development context; core skill for WP roles)",
        "Terraform (not in verified_facts; only coursework listed, no production usage)",
        "GraphQL/WPGraphQL (JD lists as integration requirement)",
    }
