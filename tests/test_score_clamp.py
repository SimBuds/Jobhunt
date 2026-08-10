"""Score-time deterministic verification of the model's extraction.

The model reads the posting and says which requirements it states; it is never
trusted about whether the candidate HAS them. Every extracted phrase is
re-verified against verified.json here, so a hallucinated match becomes a gap
and lowers the score instead of raising it. This closes the original loophole
where qwen3.5:9b returned `score=95` while listing must-haves it had not
actually matched.

The arithmetic that turns verified coverage into a number lives in
`tests/test_score_compute.py`.
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
    _verify_tier,
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


def _tier(phrases: list[str], blob: str = VERIFIED_BLOB) -> Any:
    """Verify one tier's phrases against a profile blob.

    `_verify_tier` expects an already-lowercased blob and a caller-owned `seen`
    set (shared across tiers in production so a phrase listed twice counts
    once); these tests each want a fresh one."""
    return _verify_tier(list(phrases), blob.lower(), set())


def test_verify_credits_phrases_present_in_profile() -> None:
    t = _tier(["TypeScript", "React", "Kubernetes"])
    assert t.matched == ["TypeScript", "React"]
    assert t.gaps == ["Kubernetes"]


def test_exact_hit_earns_full_credit_transferable_earns_less() -> None:
    """The grading that lets an exact-stack fit outrank a bridged one.

    Both phrases verify, so the old boolean partition scored them identically.
    Credit is what separates them now."""
    assert _tier(["React"]).credit == 1.0
    assert _tier(["Vue"]).credit == score_mod.SCORE_TRANSFERABLE_CREDIT
    assert _tier(["Kubernetes"]).credit == 0.0


# --- transferable crediting (July 2026): the clamp must honor the same
# peer-family / annotation rules the score prompt promises, instead of
# demoting every transferable match to a gap and capping the score. ---


def test_verify_credits_peer_family_member() -> None:
    """JD asks Vue; profile has React (frontend peer family). The prompt
    counts this as matched — verification must agree instead of demoting."""
    t = _tier(["Vue"])
    assert t.matched == ["Vue"]
    assert t.gaps == []


def test_verify_credits_annotated_bridge_without_family() -> None:
    """Zustand has no PEER_FAMILIES entry, but the LLM annotated the bridge
    (React) and React is verified — the annotation path must credit it."""
    assert _tier(["Zustand (transferable: React)"]).matched == [
        "Zustand (transferable: React)"
    ]


def test_verify_credits_school_project_bridge_form() -> None:
    """The prompt's coursework form: '(transferable: school project — X)'.
    The concrete tech after the em-dash is what gets verified."""
    assert _tier(["Django (transferable: school project — Python)"]).matched == [
        "Django (transferable: school project — Python)"
    ]


def test_verify_demotes_bogus_bridge() -> None:
    """A bridge naming a tech NOT in the profile fails closed — the LLM
    can't launder an unverified skill through the annotation."""
    t = _tier(["Rust (transferable: Haskell)"])
    assert t.gaps == ["Rust (transferable: Haskell)"]
    assert t.credit == 0.0


def test_verify_credits_cross_language_framework_bridge() -> None:
    """Phase 6: 'Spring Boot (transferable: Express)' — Spring Boot has no
    PEER_FAMILIES entry (deliberately: audit/tailor stay strict), so the
    credit flows only through the annotation bridge, which must verify."""
    blob = json.dumps({"skills_core": ["Express", "TypeScript"]})
    t = _tier(["Spring Boot (transferable: Express)"], blob)
    assert t.matched == ["Spring Boot (transferable: Express)"]
    assert t.gaps == []
    # Bridged, not literal — must not earn the full point.
    assert t.credit == score_mod.SCORE_TRANSFERABLE_CREDIT


def test_verify_demotes_cross_language_bogus_bridge() -> None:
    blob = json.dumps({"skills_core": ["Express", "TypeScript"]})
    assert _tier(["ASP.NET (transferable: Haskell)"], blob).gaps == [
        "ASP.NET (transferable: Haskell)"
    ]


def test_verify_demotes_unannotated_cross_language_claim() -> None:
    """Without the annotation, a cross-language claim has no path: not
    literal, no peer family, no bridge. The prompt's 'ALWAYS annotate'
    instruction is load-bearing."""
    blob = json.dumps({"skills_core": ["Express", "TypeScript"]})
    assert _tier(["Spring Boot"], blob).gaps == ["Spring Boot"]


def test_verify_demotes_llm_matched_when_not_in_profile() -> None:
    """The Pigment regression: model claimed 'Front-end frameworks' and 'AI/LLM
    tools' as matched, but 'Front-end frameworks' is not a phrase in the
    profile. Token-fallback in phrase_present means 'AI/LLM tools' DOES match
    via the `ai_tooling` blob entry — verify both behaviours."""
    t = _tier(["Front-end frameworks", "AI/LLM tools"])
    # AI/LLM tools — tokens "ai", "llm", "tools" — "ai" is in "ai_tooling",
    # "llm" is in "local llm", but "tools" is not. So it falls into gaps.
    assert "Front-end frameworks" in t.gaps
    assert "AI/LLM tools" in t.gaps


def test_verify_dedupes_repeated_phrase() -> None:
    t = _tier(["React", "React"])
    assert t.matched.count("React") + t.gaps.count("React") == 1
    assert t.credit == 1.0  # counted once, not twice


def test_seen_set_is_shared_across_tiers() -> None:
    """A phrase the model lists in BOTH tiers counts once, in tier-1.

    Tier-1 is verified first, which is the conservative direction: it stops a
    genuine hard requirement from being re-counted as a wish-list item and
    diluting the tier-1 denominator."""
    seen: set[str] = set()
    blob = VERIFIED_BLOB.lower()
    t1 = _verify_tier(["React", "TypeScript"], blob, seen)
    t2 = _verify_tier(["React", "Kubernetes"], blob, seen)
    assert t1.matched == ["React", "TypeScript"]
    assert t2.matched == []
    assert t2.gaps == ["Kubernetes"]


def test_matched_in_familiar_only_triggers_cap() -> None:
    assert _all_matched_are_familiar(["Java"], VERIFIED_BLOB) is True


def test_project_skill_counts_as_core_not_familiar() -> None:
    """PB1: a skill verified in `skills_projects` is Core-grade, so the
    Familiar-only cap must NOT fire when the only match is a project skill."""
    assert _all_matched_are_familiar(["FastAPI"], VERIFIED_BLOB) is False
    # Mixed: a project skill alongside a familiar skill is still not
    # familiar-only, so no cap.
    assert _all_matched_are_familiar(["FastAPI", "Java"], VERIFIED_BLOB) is False


def test_tier_coverage_is_graded_not_counted() -> None:
    """Coverage is credit/total, so a bridged match dilutes it below 1.0 even
    though every phrase 'matched'. The old boolean partition could not express
    this, which is why an all-transferable fit used to tie an exact one."""
    exact = _tier(["React", "TypeScript"])
    assert exact.coverage == 1.0

    bridged = _tier(["Vue", "Svelte"])  # both peer-family, neither literal
    assert bridged.matched == ["Vue", "Svelte"]
    assert bridged.coverage == score_mod.SCORE_TRANSFERABLE_CREDIT


def test_empty_tier_coverage_is_zero_not_full() -> None:
    """An empty tier must not silently read as 100% covered — `_compute_score`
    decides what an absent tier means (it folds the weight into tier-1)."""
    empty = _tier([])
    assert empty.total == 0
    assert empty.coverage == 0.0


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


def _extraction(
    must_haves: list[str],
    nice_to_haves: list[str] | None = None,
    *,
    decline_reason: str | None = None,
    ai_bonus: bool = False,
) -> Any:
    """Build a fake model response in the tiered-extraction shape.

    The model no longer returns a score, so these payloads describe only what
    the posting asks for. Everything else is computed."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "must_haves": list(must_haves),
            "nice_to_haves": list(nice_to_haves or []),
            "decline_reason": decline_reason,
            "ai_bonus_present": ai_bonus,
        }

    return fake_complete_json


@pytest.mark.asyncio
async def test_score_job_demotes_unverified_claims(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Pigment regression, under tier scoring.

    The model lists six requirements, two of which the profile does not back.
    Those two become gaps, so they drag tier-1 coverage down instead of being
    silently credited: 4/6 verified, tier-2 empty so its weight folds in,
    30 + 60*(4/6) + 5 = 75."""
    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(
            [
                "JavaScript (ES6+)",
                "TypeScript",
                "React",
                "Next.js",
                "Front-end frameworks",  # not in profile
                "AI/LLM tools",          # not fully in profile
            ],
            ai_bonus=True,
        ),
    )
    result = await score_job(_cfg(kb_dir), _full_jd_job())
    assert result.score == 75
    assert "Front-end frameworks" in result.gaps
    assert "AI/LLM tools" in result.gaps
    assert "TypeScript" in result.matched_must_haves


# --- thin-JD confidence cap ------------------------------------------------


@pytest.mark.asyncio
async def test_score_job_caps_dense_thin_snippet(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap that made the thin-JD ceiling a near no-op (2026-07-28).

    A 500-char Adzuna snippet is keyword-DENSE: it yields 4-6 phrases, all of
    which verify, so it reaches full tier-1 coverage against a bar the posting
    never actually stated. Measured on the live backlog, 12 of the 13 scores at
    78+ were 500-char snippets, which is what pinned the queue's top at 82.

    Uncapped this is 30 + 60*1.0 = 90; the ceiling pulls it to 70."""
    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(
            [
                "TypeScript",
                "React",
                "Next.js",
                "Node.js",
                "JavaScript (ES6+)",
                "FastAPI",
            ]
        ),
    )
    result = await score_job(_cfg(kb_dir), _job())  # ~44-char description
    assert result.score == 70
    assert result.gaps == []


@pytest.mark.asyncio
async def test_score_job_thin_jd_cap_never_raises(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap only lowers. A thin posting whose computed score is already
    under the ceiling keeps it: 1 of 2 verified is 30 + 60*0.5 = 60."""
    monkeypatch.setattr(
        score_mod, "complete_json", _extraction(["React", "GraphQL"])
    )
    result = await score_job(_cfg(kb_dir), _job())
    assert result.score == 60  # below cap (70) → unchanged


@pytest.mark.asyncio
async def test_score_job_long_jd_not_capped(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ceiling is length-gated. A fully-described JD with the same full
    coverage keeps its 90 — this is the discrimination the thin cap exists to
    create, and the reason a real posting can now outrank a snippet."""
    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(["React", "TypeScript", "Next.js"]),
    )
    result = await score_job(_cfg(kb_dir), _full_jd_job())
    assert result.score == 90


@pytest.mark.asyncio
async def test_score_job_low_tier1_coverage_scores_low(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing most hard requirements must land well out of the apply band.
    1 of 4 verified is 30 + 60*0.25 = 45."""
    # Gaps must be true non-peers: Angular/Svelte would legitimately
    # peer-credit via verified React.
    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(["React", "Rust", "Scala", "Kubernetes"]),
    )
    result = await score_job(_cfg(kb_dir), _full_jd_job())
    assert result.score == 45


@pytest.mark.asyncio
async def test_score_job_raises_when_nothing_extracted(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty extraction on a non-declined posting is a model failure, not a
    zero-fit job. `scan` catches JobHuntError per job and skips it, so this
    surfaces rather than quietly scoring every such posting at the base."""
    from jobhunt.errors import PipelineError

    monkeypatch.setattr(score_mod, "complete_json", _extraction([], []))
    with pytest.raises(PipelineError, match="no requirements"):
        await score_job(_cfg(kb_dir), _full_jd_job())


@pytest.mark.asyncio
async def test_score_job_promotes_nice_to_haves_when_no_must_haves(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A posting that hedges every requirement still has a real bar. Promote
    tier-2 rather than scoring it out of the queue on a phrasing quirk, so
    2 of 2 verified is 30 + 60*1.0 = 90, not the base."""
    monkeypatch.setattr(
        score_mod, "complete_json", _extraction([], ["React", "TypeScript"])
    )
    result = await score_job(_cfg(kb_dir), _full_jd_job())
    assert result.score == 90


# --- Junior-title override on Senior-band declines (2026-05-22) ---


@pytest.mark.asyncio
async def test_score_job_nullifies_senior_band_decline_for_junior_title(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the title explicitly says Junior/Intermediate/Mid/Associate, a
    Senior-band decline reason emitted by qwen3.5:9b must be nullified —
    the title is the canonical band signal."""

    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(
            ["React", ".NET"],
            decline_reason="Senior-band title; candidate YoE under typical floor",
        ),
    )
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

    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(
            ["TypeScript", "React", "Node.js"],
            decline_reason="Senior-band title; candidate YoE under typical floor",
        ),
    )
    job = Job(
        id="test:sr",
        source="test",
        external_id="sr",
        title="Senior Software Engineer",
        description="React and Node role. " * 60,  # full JD, isolates the senior cap
        company="Acme",
    )
    result = await score_job(_cfg(kb_dir), job)
    assert result.decline_reason is None
    # Computed 90; the senior ceiling pulls it to `senior_score_cap` (60).
    assert result.score == 60
    assert result.breakdown is not None
    assert "senior_band" in result.breakdown.caps_applied


@pytest.mark.asyncio
async def test_score_job_caps_senior_title_without_any_decline(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The senior ceiling is gated on the TITLE, not on a decline reason.

    Regression for the 2026-08-10 audit: the ceiling used to require a
    "Senior-band" decline_reason, but the July 2026 prompt told the model to
    stop emitting one for senior titles. Across the 650-score backlog it fired
    0 times on 62 undeclined senior-titled roles, and two full-JD senior
    postings reached 81 and 84 — above every junior role in the list."""

    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(["TypeScript", "React", "Node.js"]),  # no decline_reason
    )
    job = Job(
        id="test:sr-nodecline",
        source="test",
        external_id="sr-nodecline",
        title="Senior Software Engineer",
        description="React and Node role. " * 60,  # full JD, so thin_jd cannot bind
        company="Acme",
    )
    result = await score_job(_cfg(kb_dir), job)
    assert result.decline_reason is None
    assert result.score == 60, "senior title must be capped even when never declined"
    assert result.breakdown is not None
    assert result.breakdown.computed == 90  # the uncapped score it used to keep
    assert result.breakdown.caps_applied == ["senior_band"]


@pytest.mark.asyncio
async def test_score_job_junior_title_outranks_senior_at_equal_coverage(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junior/mid title must beat a senior one on identical coverage.

    Before the fix the backlog ran the other way: senior titles carried a
    median of 60 against 50 for explicit junior/mid ones."""

    monkeypatch.setattr(
        score_mod, "complete_json", _extraction(["TypeScript", "React", "Node.js"])
    )
    common = dict(
        source="test",
        description="React and Node role. " * 60,
        company="Acme",
    )
    junior = await score_job(
        _cfg(kb_dir),
        Job(id="t:j", external_id="j", title="Intermediate Software Developer", **common),
    )
    senior = await score_job(
        _cfg(kb_dir),
        Job(id="t:s", external_id="s", title="Staff Software Engineer", **common),
    )
    assert junior.score > senior.score, (junior.score, senior.score)
    assert junior.score == 95  # 90 computed + junior bonus, no ceiling binds
    assert senior.score == 60


@pytest.mark.asyncio
async def test_score_job_junior_bonus_cannot_escape_thin_jd_cap(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The junior bonus is added before the ceilings, so a signal-poor snippet
    stays capped rather than riding the bonus past it."""

    monkeypatch.setattr(
        score_mod, "complete_json", _extraction(["TypeScript", "React", "Node.js"])
    )
    job = Job(
        id="test:jr-thin",
        source="test",
        external_id="jr-thin",
        title="Junior Software Developer",
        description="React and Node.",  # under thin_jd_chars
        company="Acme",
    )
    result = await score_job(_cfg(kb_dir), job)
    assert result.score == 70  # thin_jd_score_cap, not 95
    assert result.breakdown is not None
    assert "thin_jd" in result.breakdown.caps_applied


@pytest.mark.asyncio
async def test_score_job_keeps_senior_band_decline_when_opted_out(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conversion is config-gated: with `include_senior_roles = false`,
    a Senior-titled posting with a Senior-band decline keeps the decline."""
    from jobhunt.config import ApplicantProfile

    # Empty extraction is allowed *because* it is declined: the model may stop
    # reading once the title disqualifies the role, and raising there would
    # mean the decline never persists and the job re-scores every scan.
    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(
            [], decline_reason="Senior-band title; candidate YoE under typical floor"
        ),
    )
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

    # Both phrases resolve into skills_familiar ONLY, and both verify, so the
    # computed score is 30 + 60*1.0 = 90. That is deliberately well above the
    # cap: if the cap did not fire, this test would read 90, not 58.
    monkeypatch.setattr(score_mod, "complete_json", _extraction(["Java", "Python"]))
    # Full JD so the 58 assertion proves the Familiar cap, not the thin ceiling.
    result = await score_job(_cfg(kb_dir), _full_jd_job())  # non-senior title
    assert result.score == 58, f"expected soft cap 58, got {result.score}"
    assert result.decline_reason is None


@pytest.mark.asyncio
async def test_score_job_llm_familiar_decline_nullified_for_junior_title(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qwen still emits the Familiar-only decline on junior titles despite
    the updated prompt (observed live 2026-07-17, adzuna Java Developer).
    The deterministic layer must nullify it and apply the 58 soft cap."""

    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(
            ["Java", "Spring Boot", "Kubernetes"],
            decline_reason=(
                "role's matched skills are all Familiar (academic/light use); "
                "not Core production experience"
            ),
        ),
    )
    result = await score_job(_cfg(kb_dir), _full_jd_job())  # non-senior title
    assert result.decline_reason is None
    assert result.score <= 58


@pytest.mark.asyncio
async def test_score_job_familiar_only_still_declines_senior_title(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decline survives for Senior-band titles: Familiar-only matches
    against a senior bar remain a misrepresentation risk (the May 2026
    Ignite Talent Java Developer ship)."""

    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(["Java", "Spring Boot", "10+ years"]),
    )
    job = Job(
        id="test:sr-java",
        source="test",
        external_id="sr-java",
        title="Senior Java Developer",
        description="Java and Spring Boot senior role. " * 40,  # full JD
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

    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(["TypeScript", "Java", "Kubernetes"]),  # mixed Core + Familiar
    )
    result = await score_job(_cfg(kb_dir), _full_jd_job())
    # 2 of 3 verified is 30 + 60*(2/3) = 70. The Familiar-only cap would have
    # pulled this to 58, so landing above that is the assertion that matters.
    assert result.score == 70
    assert result.decline_reason is None


@pytest.mark.asyncio
async def test_score_job_does_not_cap_when_already_declined(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the LLM already returned a decline_reason, the Familiar-only cap
    shouldn't overwrite it. Pre-existing decline survives."""

    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(
            ["Java"],
            decline_reason="Title is people-management (Engineering Manager)",
        ),
    )
    result = await score_job(_cfg(kb_dir), _job())
    # Pre-existing decline reason preserved.
    assert result.decline_reason == "Title is people-management (Engineering Manager)"


@pytest.mark.asyncio
async def test_score_job_full_coverage_with_ai_bonus_tops_out(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ceiling the old rubric promised but never produced: every hard
    requirement met exactly, a fully-covered wish list, and the AI bonus.
    30 + 50 + 10 + 5 = 95. Nothing scored above 82 under the old model."""
    monkeypatch.setattr(
        score_mod,
        "complete_json",
        _extraction(
            ["TypeScript", "React", "Next.js"],
            ["Node.js", "FastAPI"],
            ai_bonus=True,
        ),
    )
    result = await score_job(_cfg(kb_dir), _full_jd_job())
    assert result.score == 95
    assert result.gaps == []
