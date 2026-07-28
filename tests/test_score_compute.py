"""The deterministic score model, pinned as a calibration table.

The model no longer picks the number. It extracts the posting's requirements
into two tiers and annotates transferable bridges; the arithmetic happens in
`_compute_score`. This file pins what the numbers MEAN, because a weight change
that quietly moves the "solid fit" band is the failure mode that matters here,
not a crash.

Why the change was needed, measured on 169 live scores (2026-07-28): six
integers accounted for 136 of them and nothing ever exceeded 82. A 9B model at
temperature 0 choosing from prose bands collapses onto the band midpoints, and
the rubric's "vary the score across jobs" instruction could never work because
each job is scored in its own call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunt.config import Config, PathsConfig, PipelineConfig
from jobhunt.pipeline.score import (
    SCORE_AI_BONUS,
    SCORE_BASE,
    SCORE_TIER1_WEIGHT,
    SCORE_TIER2_WEIGHT,
    SCORE_TRANSFERABLE_CREDIT,
    ScoreWeights,
    _compute_score,
    _verify_tier,
    prompt_hash,
)

PROFILE = json.dumps(
    {
        "skills_core": ["TypeScript", "React", "Node.js", "Express", "PostgreSQL"],
        "skills_familiar": ["Java"],
        "skills_projects": ["FastAPI"],
    }
).lower()


def _tiers(must: list[str], nice: list[str] | None = None) -> tuple:
    seen: set[str] = set()
    return (
        _verify_tier(list(must), PROFILE, seen),
        _verify_tier(list(nice or []), PROFILE, seen),
    )


def _score(must: list[str], nice: list[str] | None = None, ai: bool = False) -> int:
    t1, t2 = _tiers(must, nice)
    return _compute_score(t1, t2, ai)


# --- the calibration table -------------------------------------------------


class TestCalibration:
    """What each score band should mean, in the user's own terms."""

    def test_perfect_fit_with_ai_bonus_reaches_the_ceiling(self) -> None:
        """Every hard requirement exact, wish list fully covered, AI named.
        This is the 95 the old rubric promised and never actually produced."""
        assert _score(["TypeScript", "React", "Node.js"], ["Express"], ai=True) == 95

    def test_full_core_stack_with_wishlist_gaps_stays_in_the_80s(self) -> None:
        """THE case that motivated this work.

        Every hard requirement met, but the wish list is missed. Under the old
        coverage clamp, hard requirements and nice-to-haves shared one
        denominator, so this profile computed 3/6 = 50% coverage and got capped
        at 64 — the low-60s the user reported on roles that produced real
        interviews. Tier weighting is what fixes it."""
        score = _score(
            ["TypeScript", "React", "Node.js"],  # all met
            ["Kubernetes", "Terraform", "GraphQL"],  # none met
        )
        assert score == 80
        assert 78 <= score <= 84, "should land in the solid-fit band"

    def test_half_the_hard_requirements_is_a_stretch(self) -> None:
        """~60 matches external ATS guidance that 60% is the apply threshold."""
        assert _score(["TypeScript", "React", "Kubernetes", "Terraform"]) == 60

    def test_missing_most_hard_requirements_falls_out_of_the_band(self) -> None:
        """Below the default min_score of 55, so it stops reaching the queue."""
        assert _score(["React", "Kubernetes", "Terraform", "Rust"]) == 45

    def test_nothing_matched_floors_at_base(self) -> None:
        assert _score(["Kubernetes", "Terraform", "Rust"]) == SCORE_BASE

    def test_scores_are_ordered_by_actual_fit(self) -> None:
        """The property the old model lost: dissimilar postings must separate.

        Six integers covered 136 of 169 live scores before this change."""
        perfect = _score(["TypeScript", "React"], ["Express"], ai=True)
        strong = _score(["TypeScript", "React"], ["Kubernetes"])
        partial = _score(["TypeScript", "Kubernetes"])
        weak = _score(["Kubernetes", "Terraform"])
        assert perfect > strong > partial > weak
        assert len({perfect, strong, partial, weak}) == 4


# --- the weighting rules ---------------------------------------------------


class TestWeighting:
    def test_tier1_dominates_tier2(self) -> None:
        """A missed hard requirement costs far more than a missed bonus.

        Same four requirements either way, only the tier of the miss differs:
        missing one of two hard requirements costs 25 (half of tier-1's 50),
        while missing the sole wish-list item costs 10. That gap is the whole
        reason the tiers exist — the old single-denominator clamp charged the
        same for both."""
        missed_hard = _score(["TypeScript", "Kubernetes"], ["Express"])
        missed_bonus = _score(["TypeScript", "React"], ["Kubernetes"])
        assert missed_hard == 65
        assert missed_bonus == 80
        assert missed_bonus > missed_hard

    def test_empty_tier2_folds_its_weight_into_tier1(self) -> None:
        """A posting must not score higher merely because it forgot to write a
        wish list. Full tier-1 with no tier-2 reaches the same place as full
        tier-1 plus a fully-covered tier-2."""
        no_wishlist = _score(["TypeScript", "React"])
        covered_wishlist = _score(["TypeScript", "React"], ["Express"])
        full = SCORE_BASE + SCORE_TIER1_WEIGHT + SCORE_TIER2_WEIGHT
        assert no_wishlist == covered_wishlist == full

    def test_exact_match_outranks_transferable(self) -> None:
        """The grading a boolean partition could not express. 'Vue' verifies
        through the React peer family, but it is weaker evidence than React."""
        exact = _score(["React"])
        bridged = _score(["Vue"])
        assert exact > bridged
        assert bridged == round(
            SCORE_BASE
            + (SCORE_TIER1_WEIGHT + SCORE_TIER2_WEIGHT) * SCORE_TRANSFERABLE_CREDIT
        )

    def test_ai_bonus_is_additive_and_small(self) -> None:
        without = _score(["TypeScript", "React"])
        with_ai = _score(["TypeScript", "React"], ai=True)
        assert with_ai - without == SCORE_AI_BONUS

    def test_score_never_exceeds_the_ceiling(self) -> None:
        ceiling = SCORE_BASE + SCORE_TIER1_WEIGHT + SCORE_TIER2_WEIGHT + SCORE_AI_BONUS
        assert _score(["TypeScript", "React"], ["Express", "FastAPI"], ai=True) == ceiling

    @pytest.mark.parametrize("ai", [True, False])
    def test_base_is_the_floor_for_a_zero_fit(self, ai: bool) -> None:
        """Nothing verified can only reach base (plus the AI bonus, which is
        a property of the posting, not of the candidate's fit)."""
        expected = SCORE_BASE + (SCORE_AI_BONUS if ai else 0)
        assert _score(["Rust"], ["Haskell"], ai=ai) == expected


# --- configurable weights --------------------------------------------------


def _cfg(**pipeline: object) -> Config:
    return Config(
        paths=PathsConfig(kb_dir=Path("/nonexistent-kb")),
        pipeline=PipelineConfig(**pipeline),  # type: ignore[arg-type]
    )


class TestConfigurableWeights:
    def test_config_defaults_match_the_module_constants(self) -> None:
        """The two definitions must not drift. If they do, the calibration
        table above stops describing what the app actually computes."""
        assert ScoreWeights.from_config(_cfg()) == ScoreWeights()

    def test_weights_from_config_are_what_the_score_uses(self) -> None:
        """Doubling tier-1 must actually move a tier-1-driven score."""
        t1, t2 = _tiers(["TypeScript", "Kubernetes"])  # 50% tier-1 coverage
        default = _compute_score(t1, t2, False, ScoreWeights.from_config(_cfg()))
        doubled = _compute_score(
            t1, t2, False, ScoreWeights.from_config(_cfg(score_tier1_weight=100))
        )
        assert default == 60  # 30 + 60*0.5
        assert doubled == 85  # 30 + 110*0.5

    def test_transferable_credit_is_configurable(self) -> None:
        """Set it to 1.0 and a bridged match stops being penalised."""
        seen: set[str] = set()
        full = ScoreWeights.from_config(_cfg(score_transferable_credit=1.0))
        assert _verify_tier(["Vue"], PROFILE, seen, full).credit == 1.0

    def test_base_is_configurable(self) -> None:
        assert _score([]) == SCORE_BASE  # sanity: empty tier is base
        t1, t2 = _tiers(["Rust"])
        assert _compute_score(t1, t2, False, ScoreWeights.from_config(_cfg())) == 30
        assert (
            _compute_score(t1, t2, False, ScoreWeights.from_config(_cfg(score_base=0)))
            == 0
        )


class TestPromptHashCoversWeights:
    """A weight change must re-score the backlog.

    Without this, changing a coefficient leaves every existing score computed
    under the old one, mixed in with new scores and indistinguishable from
    them — the queue would be sorted on two different scales at once.
    """

    def test_same_config_is_stable(self) -> None:
        assert prompt_hash(_cfg()) == prompt_hash(_cfg())

    @pytest.mark.parametrize(
        "override",
        [
            {"score_base": 25},
            {"score_tier1_weight": 55},
            {"score_tier2_weight": 15},
            {"score_ai_bonus": 8},
            {"score_transferable_credit": 0.8},
        ],
    )
    def test_every_weight_moves_the_hash(self, override: dict) -> None:
        assert prompt_hash(_cfg(**override)) != prompt_hash(_cfg())

    def test_unrelated_pipeline_knob_does_not_move_the_hash(self) -> None:
        """Only inputs that change the SCORE belong in the hash. Bumping an
        unrelated knob must not force a pointless full re-score."""
        assert prompt_hash(_cfg(cover_max_words=300)) == prompt_hash(_cfg())
