# IMPLEMENT.md

## Current work: recalibrate scoring so it discriminates

**Request restated:** scores wall at 82 and never pass it, while jobs that
yielded real interviews would score around 62. Find where the weighting is
wrong and fix it so strong fits can reach the top bands and interviewable
roles stop being crushed into the low 60s.

## Evidence (169 live scores survive the db reset)

Distribution of every non-declined score in `data/jobhunt.db`:

```
82 x12   78 x1   72 x5   68 x1   65 x1   64 x2   62 x9
58 x48   57 x1   56 x5   54 x5   52 x34  50 x3
48 x1    45 x2   42 x10  40 x28  30 x1   (0 x86 = declines)
```

Five findings, each verified in code, not inferred:

1. **The score is categorical, not a ranking.** Six values (82, 58, 52, 40,
   62, 72) account for 136 of 169 scores. Nothing has ever scored above 82.
   The rubric tells the model "78-84 is the default band for solid fits" and
   qwen3.5:9b at temperature 0.0 deterministically picks the middle of
   whatever band it lands in. The rubric's "scores must vary, perturb by 1-3"
   instruction cannot work because each job is scored in its own LLM call,
   so the model never sees the batch it is supposed to vary within.

2. **The 82 ceiling is the LLM's own conservatism, unfixable by prompt
   nudging.** Bands above 84 require "all must-haves matched". Real JDs mix
   hard requirements with wish lists, so the model essentially never chooses
   those bands, and `_clamp_by_coverage` only lowers, never raises. The two
   effects compound into a hard wall at 82.

3. **The thin-JD confidence cap is bypassed by keyword-dense snippets.**
   `score.py:100-107` applies `thin_jd_score_cap=70` only when fewer than 3
   must-haves were extracted. 500-char Adzuna snippets are keyword-dense, so
   they routinely yield 4-6 extracted phrases, hit 100% coverage, and pass
   through uncapped. Measured: **12 of the 13 scores at 78+ are 500-char
   snippets.** The 2026-05-31 ZoomInfo comment in the code says this exact
   failure ("thin snippets float to 82-88 and outrank fully-described
   full-JD roles") was the reason the cap exists. The fix was incomplete.

4. **The coverage clamp punishes the empirically interviewable band.** The
   clamp treats coverage of an unweighted phrase list as fit: 60-79% caps at
   79, under 60% caps at 64. External ATS-calibration guidance says a 65-75%
   keyword match is competitive (interviews happen there routinely) and that
   candidates should apply from roughly 60% up. A real JD with 8 requirements
   and a 4-item wish list, of which Casey matches the whole core stack but
   misses wish-list items, lands at 55-70% flat coverage and gets capped into
   exactly the low-60s band the user observed on interview-yielding roles.
   The clamp's denominator is the problem: hard requirements and nice-to-haves
   count equally.

5. **The 40 and 58 spikes are floor and cap artifacts.** `score=40` is the
   hard floor bump for non-declined sub-30 outputs (28 jobs). The 55-59
   stretch-band and Familiar-junior instructions pile 48 jobs onto 58. More
   evidence the LLM's integer carries little information beyond the band.

## Design direction

Two options considered.

**Option A (rejected): retune the prompt rubric and clamp bands only.** Small
diff, but keeps the root cause: a 9B model at temperature 0 picking an integer
from prose bands will always collapse onto attractors. The wall moves, the
compression stays.

**Option B (chosen): the LLM extracts and classifies, the code computes the
score.** This is the project's existing trajectory, stated in PLAN.md honesty
enforcement item 4: the coverage clamp already exists because the model
"cannot inflate its own band", and `_verify_against_profile` already
re-verifies every phrase deterministically. This plan completes that
trajectory. The LLM does what a 9B model is good at (reading the JD, splitting
hard requirements from nice-to-haves, naming transferable bridges, flagging
decline conditions). The code does what code is good at (arithmetic over
verified facts, reproducibly, with tunable weights).

Decided with the user 2026-07-28: Option B, all four phases including the
migration in Phase 4.

### Score model (defaults, all become config knobs in Phase 3)

```
score = base
      + tier1_weight * tier1_credit    # hard requirements, dominant term
      + tier2_weight * tier2_credit    # nice-to-haves, minor term
      + ai_bonus                       # JD names AI/LLM tooling

base          = 30
tier1_weight  = 50
tier2_weight  = 10
ai_bonus      = 5
transferable_credit = 0.7   # exact match counts 1.0
```

- `tierN_credit` = sum of per-phrase credit / phrase count, where an exact
  verified match earns 1.0 and a `(transferable: X)` match earns 0.7.
- Empty tier-2 list (common on snippets): its weight folds into tier 1
  rather than awarding free points.
- Calibration this produces: all tier-1 exact plus full wish list plus AI
  bonus = 95 (the "rare 95+" the old rubric promised but never delivered).
  All tier-1 exact, half the wish list = high 80s. Full core-stack match
  with wish-list gaps = low 80s (the interviewable profile the user
  described, currently scoring 62). Half the hard requirements = around 60
  (stretch band, matching external "apply from 60%" guidance). Weak fits
  fall out of the applyable band naturally.
- Existing deterministic caps compose unchanged on top via `min()`:
  thin-JD 70, senior-band 70, Familiar-only 54/58. The score-0-decline
  convention and the floor-40 bump disappear because the code now owns the
  integer (declines stay 0, base=30 is the natural floor).

## Phases

### Phase 1: apply the thin-JD cap by description length alone [ ]

**Goal:** a sub-`thin_jd_chars` description can never score above
`thin_jd_score_cap`, regardless of how many must-haves were extracted.

- Files: `src/jobhunt/pipeline/score.py`, `tests/test_score_clamp.py`.
- Change: hoist the length check out of the `must_have_count < 3` branch so
  the cap applies after whichever clamp path ran. The `< 3` branch keeps its
  current role (skip the coverage clamp on tiny denominators).
- Reuse audit: no new function needed, the cap and knobs exist. Searched
  `grep -n "thin_jd" src/ tests/`: only score.py and its tests.
- Verify: new test, a 500-char JD with 6/6 matched phrases caps at 70 (fails
  today, currently returns the raw score). Existing thin-JD tests still pass.
  `pytest -q` green.

### Phase 2: LLM extracts tiered requirements, code computes the score [ ]

**Goal:** replace the LLM-chosen integer with a deterministic score computed
from verified tier-1/tier-2 coverage.

- Files: `kb/prompts/score.md`, `src/jobhunt/pipeline/score.py`,
  `tests/test_score_clamp.py` (plus a new `tests/test_score_compute.py`).
- Prompt: schema drops `score`, gains `must_haves` and `nice_to_haves`
  (phrase arrays, transferable annotations kept). Rubric prose (bands,
  "default band", perturbation instruction) is deleted. Extraction guidance,
  transferable table, decline triggers, and `ai_bonus_present` stay.
- Code: new `_compute_score(tier1_matched, tier1_gaps, tier2_matched,
  tier2_gaps, ai_bonus, weights) -> int`. Both tiers run through the existing
  `_verify_against_profile` (reused unchanged, it is the matching engine).
  `_clamp_by_coverage` and `_coerce_score` retire with the LLM integer.
  Decline flow (LLM `decline_reason` plus the junior/senior/Familiar
  deterministic overrides) unchanged.
- Reuse audit: searched `grep -n "def _" src/jobhunt/pipeline/score.py` and
  `grep -rn "compute.*score\|score.*compute" src/`. Only `_clamp_by_coverage`
  computes anything score-like, and it is the piece being replaced. The
  transferable-credit split reuses `_bridge_of` to detect annotated matches.
- Verify: unit tests over fixture extractions pinning the calibration table
  above (exact-full=90+, core-match-with-wishlist-gaps=low 80s,
  half-tier1=about 60). Manual integration run per AGENTS.md testing rules:
  re-score the three known jobs (Saje, Tiger, Viv) plus the faire full JD
  with real Ollama and report observed spread. `pytest -q` green.

### Phase 3: weights in config, folded into prompt_hash [ ]

**Goal:** weight changes are tunable in config.toml and trigger re-scoring.

- Files: `src/jobhunt/config.py`, `src/jobhunt/pipeline/score.py`,
  `tests/test_score_compute.py`, README config table.
- Change: `[pipeline]` gains `score_base`, `score_tier1_weight`,
  `score_tier2_weight`, `score_ai_bonus`, `score_transferable_credit`
  (defaults above). `prompt_hash` appends a canonical serialization of the
  five values so a weight change re-scores the backlog, same mechanism that
  already covers prompt and profile edits.
- Reuse audit: config knob pattern copied from the existing `thin_jd_*`
  knobs. `prompt_hash` extended in place, no new function.
- Verify: changing a weight in a tmp config changes `prompt_hash` (test),
  defaults reproduce Phase 2's pinned scores exactly (test). `pytest -q`.

### Phase 4: persist the score breakdown for outcome calibration [ ]

**Goal:** every score row records its components so `config calibrate` can
eventually tune weights against real interview outcomes.

- Files: `migrations/0010_score_breakdown.sql` (new nullable TEXT column
  `breakdown` on `scores`), `src/jobhunt/db.py`, `src/jobhunt/pipeline/score.py`,
  `src/jobhunt/commands/config_cmd.py` (calibrate prints per-band mean
  tier-1 coverage when breakdowns exist), tests.
- **Risky tier: schema migration.** Per the contract this needs an explicit
  go-ahead at execution time even within the approved plan.
- Reuse audit: migration file pattern follows 0001-0009. The breakdown JSON
  reuses the component dict `_compute_score` already builds internally.
- Verify: migration applies on a copy of the live db, new scores carry
  breakdown JSON, old rows read back as NULL without breaking `list` or
  `calibrate`. `pytest -q`.

## Expected outcome

- Snippets stop owning the top of the queue (Phase 1, immediately).
- Strong full-JD fits can reach 85-95, and the low-80s become the home of
  the core-stack-match-with-wishlist-gaps profile that actually interviews,
  instead of 62 (Phase 2).
- "Scoring weights" become literal numbers in config.toml the user can tune,
  with automatic re-scoring on change (Phase 3), and enough per-score data
  to tune them against interview outcomes later (Phase 4).

## External calibration references

ATS-checker guidance converges on 75%+ keyword match as competitive, 65%
often sufficient in practice, and roughly 60% as the apply threshold.
Callback-rate figures step up meaningfully between 70% and 85% match.
Sources: jobscan.co, airesume.guru/blog/ats-score-resume-match-rates,
atschecker.ai/guides/ats-score-explained,
job200.com/blog/how-does-an-ats-score-your-resume-the-full-breakdown.

## Deferred / follow-up

- **Per-attempt progress output in the tailor retry loop.** Three failing
  attempts produce up to three minutes of silence followed by one error line,
  which reads as a hang. Recorded in PLAN.md under honesty enforcement item 3
  as a known gap. Lives in `apply_cmd`.
