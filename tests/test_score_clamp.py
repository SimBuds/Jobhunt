"""Score-time deterministic coverage clamp.

Closes the loophole where qwen3.5:9b returned `score=95` while listing must-haves
it hadn't actually matched in `matched_must_haves`. We re-partition the LLM's
must-have list against verified.json ourselves and cap the score to the band
the deterministic coverage justifies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobhunt.config import Config, GatewayConfig, PathsConfig
from jobhunt.models import Job
from jobhunt.pipeline import score as score_mod
from jobhunt.pipeline.score import (
    _all_matched_are_familiar,
    _clamp_by_coverage,
    _coerce_score,
    _coverage_pct,
    _verify_against_profile,
    score_job,
)

# --- pure-function tests ---------------------------------------------------


VERIFIED_BLOB = json.dumps(
    {
        "skills_core": [
            "JavaScript (ES6+)",
            "TypeScript",
            "React",
            "Next.js",
            "Node.js",
            "Shopify (Liquid, Custom Themes)",
        ],
        "skills_familiar": ["Python", "Java"],
        "skills_projects": ["FastAPI", "Redis"],
        "ai_tooling": "Local LLM hosting via Ollama; prompt engineering for code generation.",
    }
)


@pytest.mark.parametrize(
    "raw,expected",
    [(40, 40), (0, 0), (87.0, 87), (72.6, 72), ("55", 55), (" 63 ", 63), ("70.0", 70)],
)
def test_coerce_score_accepts_numbers_and_numeric_strings(raw: Any, expected: int) -> None:
    assert _coerce_score(raw, "job:1") == expected


@pytest.mark.parametrize("raw", [None, "", "n/a", True, False, [], {}])
def test_coerce_score_rejects_unusable_values(raw: Any) -> None:
    from jobhunt.errors import PipelineError

    with pytest.raises(PipelineError):
        _coerce_score(raw, "job:1")


def test_coerce_phrase_list_known_keys() -> None:
    from jobhunt.pipeline.score import _coerce_phrase_list

    assert _coerce_phrase_list(
        ["React", {"phrase": "Node.js"}, {"skill": "TypeScript"}]
    ) == ["React", "Node.js", "TypeScript"]


def test_coerce_phrase_list_recovers_unknown_dict_keys() -> None:
    # Live-captured shapes from the 2026-06-11 scan: qwen invents a new key
    # set per call. The phrase is the first string value; the match-status
    # vocabulary comes second and must NOT be picked up.
    from jobhunt.pipeline.score import _coerce_phrase_list

    assert _coerce_phrase_list(
        [
            {
                "requirement": "2-5 years of professional experience",
                "match_status": "exact",
            },
            {"tech": "React", "match_type": "transferable (Vue)"},
        ]
    ) == ["2-5 years of professional experience", "React"]


def test_coerce_phrase_list_drops_dicts_without_string_values() -> None:
    from jobhunt.pipeline.score import _coerce_phrase_list

    assert _coerce_phrase_list([{"count": 3}, {}, {"flag": True}]) == []


def test_verify_credits_phrases_present_in_profile() -> None:
    matched, gaps = _verify_against_profile(
        ["TypeScript", "React"], ["Kubernetes"], VERIFIED_BLOB
    )
    assert matched == ["TypeScript", "React"]
    assert gaps == ["Kubernetes"]


# --- transferable crediting (July 2026): the clamp must honor the same
# peer-family / annotation rules the score prompt promises, instead of
# demoting every transferable match to a gap and capping the score. ---


def test_verify_credits_peer_family_member() -> None:
    """JD asks Vue; profile has React (frontend peer family). The prompt
    counts this as matched — the clamp must agree instead of demoting."""
    matched, gaps = _verify_against_profile([], ["Vue"], VERIFIED_BLOB)
    assert matched == ["Vue"]
    assert gaps == []


def test_verify_credits_annotated_bridge_without_family() -> None:
    """Zustand has no PEER_FAMILIES entry, but the LLM annotated the bridge
    (React) and React is verified — the annotation path must credit it."""
    matched, gaps = _verify_against_profile(
        ["Zustand (transferable: React)"], [], VERIFIED_BLOB
    )
    assert matched == ["Zustand (transferable: React)"]


def test_verify_credits_school_project_bridge_form() -> None:
    """The prompt's coursework form: '(transferable: school project — X)'.
    The concrete tech after the em-dash is what gets verified."""
    matched, gaps = _verify_against_profile(
        ["Django (transferable: school project — Python)"], [], VERIFIED_BLOB
    )
    assert matched == ["Django (transferable: school project — Python)"]


def test_verify_demotes_bogus_bridge() -> None:
    """A bridge naming a tech NOT in the profile fails closed — the LLM
    can't launder an unverified skill through the annotation."""
    matched, gaps = _verify_against_profile(
        ["Rust (transferable: Haskell)"], [], VERIFIED_BLOB
    )
    assert gaps == ["Rust (transferable: Haskell)"]


def test_verify_credits_cross_language_framework_bridge() -> None:
    """Phase 6: 'Spring Boot (transferable: Express)' — Spring Boot has no
    PEER_FAMILIES entry (deliberately: audit/tailor stay strict), so the
    credit flows only through the annotation bridge, which must verify."""
    blob = json.dumps({"skills_core": ["Express", "TypeScript"]})
    matched, gaps = _verify_against_profile(
        ["Spring Boot (transferable: Express)"], [], blob
    )
    assert matched == ["Spring Boot (transferable: Express)"]
    assert gaps == []


def test_verify_demotes_cross_language_bogus_bridge() -> None:
    blob = json.dumps({"skills_core": ["Express", "TypeScript"]})
    matched, gaps = _verify_against_profile(
        ["ASP.NET (transferable: Haskell)"], [], blob
    )
    assert gaps == ["ASP.NET (transferable: Haskell)"]


def test_verify_demotes_unannotated_cross_language_claim() -> None:
    """Without the annotation, a cross-language claim has no path: not
    literal, no peer family, no bridge. The prompt's 'ALWAYS annotate'
    instruction is load-bearing."""
    blob = json.dumps({"skills_core": ["Express", "TypeScript"]})
    matched, gaps = _verify_against_profile(["Spring Boot"], [], blob)
    assert gaps == ["Spring Boot"]


def test_verify_demotes_llm_matched_when_not_in_profile() -> None:
    """The Pigment regression: model claimed 'Front-end frameworks' and 'AI/LLM
    tools' as matched, but 'Front-end frameworks' is not a phrase in the
    profile. Token-fallback in phrase_present means 'AI/LLM tools' DOES match
    via the `ai_tooling` blob entry — verify both behaviours."""
    matched, gaps = _verify_against_profile(
        ["Front-end frameworks", "AI/LLM tools"], [], VERIFIED_BLOB
    )
    # AI/LLM tools — tokens "ai", "llm", "tools" — "ai" is in "ai_tooling",
    # "llm" is in "local llm", but "tools" is not. So it falls into gaps.
    assert "Front-end frameworks" in gaps
    assert "AI/LLM tools" in gaps


def test_verify_dedupes_overlap_between_matched_and_gaps() -> None:
    matched, gaps = _verify_against_profile(["React"], ["React"], VERIFIED_BLOB)
    assert matched.count("React") + gaps.count("React") == 1


def test_matched_in_familiar_only_triggers_cap() -> None:
    assert _all_matched_are_familiar(["Java"], VERIFIED_BLOB) is True


def test_project_skill_counts_as_core_not_familiar() -> None:
    """PB1: a skill verified in `skills_projects` is Core-grade, so the
    Familiar-only cap must NOT fire when the only match is a project skill."""
    assert _all_matched_are_familiar(["FastAPI"], VERIFIED_BLOB) is False
    # Mixed: a project skill alongside a familiar skill is still not
    # familiar-only, so no cap.
    assert _all_matched_are_familiar(["FastAPI", "Java"], VERIFIED_BLOB) is False


def test_coverage_pct_handles_empty() -> None:
    assert _coverage_pct([], []) == 100


def test_coverage_pct_rounds() -> None:
    assert _coverage_pct(["a"], ["b", "c"]) == 33  # 1/3


@pytest.mark.parametrize(
    "raw,coverage,expected",
    [
        (95, 100, 95),  # full coverage — keep
        (95, 80, 89),   # one missing — cap at 89
        (95, 67, 79),   # two missing of six — cap at 79 (Pigment scenario)
        (95, 50, 64),   # three+ missing — cap at 64
        (95, 0, 64),    # nothing matches — cap at 64
        (60, 100, 60),  # clamp never raises a score
        (60, 50, 60),   # raw already below cap — leave alone
        (89, 80, 89),   # at-the-cap — unchanged
    ],
)
def test_clamp_by_coverage(raw: int, coverage: int, expected: int) -> None:
    assert _clamp_by_coverage(raw, coverage) == expected


# --- end-to-end test (mocks complete_json, no Ollama call) -----------------


@pytest.fixture
def kb_dir(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "profile").mkdir(parents=True)
    (kb / "policies").mkdir()
    (kb / "prompts").mkdir()
    (kb / "profile" / "verified.json").write_text(VERIFIED_BLOB)
    (kb / "policies" / "tailoring-rules.md").write_text("policy text")
    # Minimal score prompt so load_prompt works.
    (kb / "prompts" / "score.md").write_text(
        "---\n"
        "task: score\n"
        "temperature: 0.0\n"
        "schema:\n"
        "  type: object\n"
        "  properties: {score: {type: integer}}\n"
        "---\n"
        "## SYSTEM\nScore.\n## USER\n{{title}} {{description}}\n"
    )
    return kb


def _cfg(kb: Path) -> Config:
    return Config(
        paths=PathsConfig(kb_dir=kb),
        gateway=GatewayConfig(tasks={"score": "qwen3.5:9b"}),
    )


def _job() -> Job:
    return Job(
        id="test:1",
        source="test",
        external_id="1",
        title="Front-end Engineer",
        description="React + TypeScript, AI/LLM tooling required.",
        company="Pigment",
    )


def _full_jd_job(**overrides: Any) -> Job:
    """A job whose description clears `thin_jd_chars`, so the thin-JD ceiling
    is out of the picture.

    `_job()` is ~44 chars, i.e. thin. Tests that care about the *coverage*
    clamp must not silently also be exercising the thin-JD cap, or their
    assertion stops describing what the test name claims."""
    fields: dict[str, Any] = {
        "id": "test:full",
        "source": "test",
        "external_id": "full",
        "title": "Front-end Engineer",
        "description": "React and TypeScript role. " * 50,  # ~1350 chars > 800
        "company": "Pigment",
    }
    fields.update(overrides)
    return Job(**fields)


@pytest.mark.asyncio
async def test_score_job_clamps_when_llm_inflates(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the Pigment regression. LLM returns score=95 with two phrases
    in matched_must_haves that the verified profile does not actually back."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 95,
            "matched_must_haves": [
                "JavaScript (ES6+)",
                "TypeScript",
                "React",
                "Next.js",
                "Front-end frameworks",  # not in profile
                "AI/LLM tools",           # not fully in profile
            ],
            "gaps": [],
            "decline_reason": None,
            "ai_bonus_present": True,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    # Full-length JD so the thin-JD ceiling stays out of the way and the
    # assertion measures the coverage clamp alone.
    result = await score_job(_cfg(kb_dir), _full_jd_job())
    # 4/6 matched = 67% coverage → cap at 79.
    assert result.score == 79
    assert "Front-end frameworks" in result.gaps
    assert "AI/LLM tools" in result.gaps
    assert "TypeScript" in result.matched_must_haves


# --- tiny-denominator carve-out + thin-JD confidence cap (2026-05-31) ---


@pytest.mark.asyncio
async def test_score_job_caps_thin_jd_snippet(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thin-JD inflation fix (2026-05-31 audit). Adzuna's ~500-char snippets
    yield only 1-2 phrases, so the coverage clamp is skipped (1/2 denominator
    over-penalizes). But the raw LLM score must NOT pass through unbounded — the
    model can't penalize gaps it can't see, so snippets float to 82-88 and
    outrank full-JD roles. Cap them at thin_jd_score_cap (70). `_job()` has a
    ~46-char description (< thin_jd_chars=800), so the cap fires."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 78,
            "matched_must_haves": ["React"],
            "gaps": ["GraphQL"],  # 1 matched, 1 gap = 2 total < 3 threshold
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    # Pre-fix this stood at raw 78. Now the thin-JD ceiling caps it at 70.
    assert result.score == 70


@pytest.mark.asyncio
async def test_score_job_caps_dense_thin_snippet(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap that made the thin-JD ceiling a near no-op (2026-07-28).

    The cap used to live inside the `must_have_count < 3` branch, so it only
    fired on snippets the model found almost nothing in. But a 500-char Adzuna
    snippet is keyword-DENSE: it routinely yields 4-6 phrases, all of which
    verify, giving 100% coverage against a denominator the JD was never big
    enough to justify. Those sailed past the coverage clamp AND the ceiling.

    Measured on the live 2026-07-28 backlog: 12 of the 13 scores at 78+ were
    500-char snippets, which is what pinned the top of the queue at 82.

    Phrase count was never the right signal. How much JD text the model got to
    read is, so the gate is description length alone."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 82,
            # 6 phrases, every one backed by the profile → 100% coverage, and
            # well past the <3 tiny-denominator carve-out.
            "matched_must_haves": [
                "TypeScript",
                "React",
                "Next.js",
                "Node.js",
                "JavaScript (ES6+)",
                "FastAPI",
            ],
            "gaps": [],
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())  # ~44-char description

    # Pre-fix: 100% coverage kept the raw 82 and the ceiling never ran.
    assert result.score == 70
    assert result.gaps == []


@pytest.mark.asyncio
async def test_score_job_thin_jd_cap_never_raises(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap only lowers. A thin-JD raw score already below the ceiling is
    left alone — preserving the original carve-out intent of not dragging a
    signal-poor 1/1 coverage down to the coverage-clamp's 64 floor."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 64,
            "matched_must_haves": ["React"],
            "gaps": ["GraphQL"],  # 2 total < 3 threshold
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    assert result.score == 64  # below cap (70) → unchanged


@pytest.mark.asyncio
async def test_score_job_long_jd_few_musthaves_not_capped(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The thin-JD cap is length-gated. A long, fully-described JD that merely
    happened to yield <3 must-haves (e.g. a manual `apply --url` fetch) is
    exempt — its raw score stands. Only short snippets (< thin_jd_chars) are
    treated as signal-poor."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 82,
            "matched_must_haves": ["React"],
            "gaps": ["GraphQL"],  # 2 total < 3 threshold
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    long_job = Job(
        id="test:long",
        source="manual",
        external_id="long",
        title="Front-end Engineer",
        description="React and TypeScript role. " * 50,  # ~1300 chars > 800
        company="Acme",
    )
    result = await score_job(_cfg(kb_dir), long_job)
    assert result.score == 82  # length gate exempts full JDs


@pytest.mark.asyncio
async def test_score_job_still_clamps_when_denominator_sufficient(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tiny-denominator carve-out only applies under 3 must-haves total.
    Three or more must-haves still get clamped — protects against the Pigment
    regression that originally motivated the clamp."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        # Gaps must be true non-peers: Angular/Svelte would now legitimately
        # peer-credit via verified React (July 2026 transferable crediting).
        return {
            "score": 95,
            "matched_must_haves": ["React"],
            "gaps": ["Rust", "Scala", "Kubernetes"],  # 1/4 = 25% coverage
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    # 1/4 = 25% < 60% → cap at 64.
    assert result.score == 64


# --- Junior-title override on Senior-band declines (2026-05-22) ---


@pytest.mark.asyncio
async def test_score_job_nullifies_senior_band_decline_for_junior_title(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the title explicitly says Junior/Intermediate/Mid/Associate, a
    Senior-band decline reason emitted by qwen3.5:9b must be nullified —
    the title is the canonical band signal."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 60,
            "matched_must_haves": ["React"],
            "gaps": [".NET"],
            "decline_reason": "Senior-band title; candidate YoE under typical floor",
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    job = Job(
        id="test:jr",
        source="test",
        external_id="jr",
        title="Junior Full Stack Developer (.NET / Cloud)",
        description="React + .NET. Junior role.",
        company="Acme",
    )
    result = await score_job(_cfg(kb_dir), job)
    assert result.decline_reason is None, result.decline_reason


@pytest.mark.asyncio
async def test_score_job_senior_decline_converts_to_cap_when_opted_in(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Senior-band exposure (July 2026): with `include_senior_roles = true`
    (the ApplicantConfig default), a Senior-band decline the model still
    emits converts to a ≤70 confidence cap instead — the role stays
    applyable in the stretch band."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 88,
            "matched_must_haves": ["TypeScript", "React", "Node.js"],
            "gaps": [],
            "decline_reason": "Senior-band title; candidate YoE under typical floor",
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    job = Job(
        id="test:sr",
        source="test",
        external_id="sr",
        title="Senior Software Engineer",
        description="React + Node.",
        company="Acme",
    )
    result = await score_job(_cfg(kb_dir), job)
    assert result.decline_reason is None
    assert result.score == 70  # raw 88, full coverage, senior ceiling


@pytest.mark.asyncio
async def test_score_job_keeps_senior_band_decline_when_opted_out(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conversion is config-gated: with `include_senior_roles = false`,
    a Senior-titled posting with a Senior-band decline keeps the decline."""
    from jobhunt.config import ApplicantProfile

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 0,
            "matched_must_haves": [],
            "gaps": [],
            "decline_reason": "Senior-band title; candidate YoE under typical floor",
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    cfg = Config(
        paths=PathsConfig(kb_dir=kb_dir),
        gateway=GatewayConfig(tasks={"score": "qwen3.5:9b"}),
        applicant=ApplicantProfile(include_senior_roles=False),
    )
    job = Job(
        id="test:sr",
        source="test",
        external_id="sr",
        title="Senior Software Engineer",
        description="React + Node.",
        company="Acme",
    )
    result = await score_job(cfg, job)
    assert result.decline_reason is not None
    assert "senior-band" in result.decline_reason.lower()


# --- Familiar-only-fit cap (May 2026, Phase 10.2) ---


@pytest.mark.asyncio
async def test_score_job_familiar_only_soft_band_for_junior_title(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """July 2026 soft band: a NON-senior title whose matches are all
    Familiar-bucket skills caps at 58 WITHOUT a decline — the role stays
    visible in the 55-59 stretch band (coachable-junior story)."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 78,  # LLM was overly generous
            "matched_must_haves": ["Java", "Spring Boot"],
            "gaps": ["10+ years"],
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())  # title: Front-end Engineer
    assert result.score == 58, f"expected soft cap 58, got {result.score}"
    assert result.decline_reason is None


@pytest.mark.asyncio
async def test_score_job_llm_familiar_decline_nullified_for_junior_title(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qwen still emits the Familiar-only decline on junior titles despite
    the updated prompt (observed live 2026-07-17, adzuna Java Developer).
    The deterministic layer must nullify it and apply the 58 soft cap."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 62,
            "matched_must_haves": ["Java", "Spring Boot"],
            "gaps": ["Kubernetes"],
            "decline_reason": (
                "role's matched skills are all Familiar (academic/light use); "
                "not Core production experience"
            ),
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())  # non-senior title
    assert result.decline_reason is None
    assert result.score <= 58


@pytest.mark.asyncio
async def test_score_job_familiar_only_still_declines_senior_title(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decline survives for Senior-band titles: Familiar-only matches
    against a senior bar remain a misrepresentation risk (the May 2026
    Ignite Talent Java Developer ship)."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 78,
            "matched_must_haves": ["Java", "Spring Boot"],
            "gaps": ["10+ years"],
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    job = Job(
        id="test:sr-java",
        source="test",
        external_id="sr-java",
        title="Senior Java Developer",
        description="Java + Spring Boot. Senior role.",
        company="Acme",
    )
    result = await score_job(_cfg(kb_dir), job)
    assert result.score <= 54, f"expected senior cap, got {result.score}"
    assert result.decline_reason is not None
    assert "familiar" in result.decline_reason.lower()


@pytest.mark.asyncio
async def test_score_job_does_not_cap_when_core_skill_also_matched(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When at least one matched must-have is a Core skill (TypeScript,
    React, etc.), the Familiar-only cap should NOT fire. Roles with mixed
    Core+Familiar matches are legitimate fits."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 78,
            "matched_must_haves": ["TypeScript", "Java"],  # mixed
            "gaps": ["Kubernetes"],
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    # Phase 2.5 tiny-denominator carve-out applies (3 must-haves total), so
    # the clamp kicks in: 2/3 = 67% → cap at 79. Result stays at 78 since
    # raw was already at 78. Confirm no Familiar-only cap fires.
    assert result.score >= 64
    assert result.decline_reason is None


@pytest.mark.asyncio
async def test_score_job_does_not_cap_when_already_declined(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the LLM already returned a decline_reason, the Familiar-only cap
    shouldn't overwrite it. Pre-existing decline survives."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 0,
            "matched_must_haves": ["Java"],
            "gaps": [],
            "decline_reason": "Title is people-management (Engineering Manager)",
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    # Pre-existing decline reason preserved.
    assert result.decline_reason == "Title is people-management (Engineering Manager)"


@pytest.mark.asyncio
async def test_score_job_keeps_score_when_coverage_full(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 95,
            "matched_must_haves": ["TypeScript", "React", "Next.js"],
            "gaps": [],
            "decline_reason": None,
            "ai_bonus_present": True,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    # Full-length JD: 100% coverage keeps the raw score only when the posting
    # was substantial enough to trust. The thin-JD variant is covered by
    # `test_score_job_caps_dense_thin_snippet` below.
    result = await score_job(_cfg(kb_dir), _full_jd_job())
    assert result.score == 95
    assert result.gaps == []
