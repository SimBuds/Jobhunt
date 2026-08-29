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

### Phase 1: apply the thin-JD cap by description length alone [x] DONE

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

**Outcome (observed, not predicted):**

- The cap moved out of the `must_have_count < 3` branch and now runs after
  whichever clamp path was taken. The `< 3` branch keeps its separate job.
- Two existing tests failed on the change and were **not** simply re-baselined.
  `test_score_job_clamps_when_llm_inflates` and
  `test_score_job_keeps_score_when_coverage_full` both used the `_job()`
  fixture, whose description is ~44 chars, i.e. thin. They are coverage-clamp
  tests that were silently also exercising the thin path, so their assertions
  had stopped describing what their names claim. Added a `_full_jd_job()`
  helper (~1350 chars) and pointed both at it, making each test single-purpose.
- New `test_score_job_caps_dense_thin_snippet` pins the actual gap. Verified it
  fails without the change: restoring the old `must_have_count < 3 and ...`
  gate fails exactly that one test and nothing else.
- `ruff` flagged SIM108 on the new if/else; rewritten as a ternary.
- `pytest -q`: 1070 passed, up from 1069. `ruff` and `mypy` clean.
- **Live projection over the 169 stored scores:** 93 are thin, and **17 sit
  above the cap and are corrected to 70**. Afterwards the only posting left
  above 70 is the one full JD in the backlog (faire, 12,530 chars, 82). The
  top of the queue stops being 12 Adzuna snippets and one real posting.
- **Real end-to-end re-score** (Ollama, not mocked): *Software Engineer @
  Nylas*, 500-char description, stored at 82. Re-scored **70** with
  `matched=7 gaps=0` — 100% coverage on 7 phrases, textbook confirmation of
  the bypass this phase closes (7 phrases is far past the `< 3` carve-out, so
  the old gate never fired). One earlier attempt died on an Ollama-side CUDA
  illegal-memory-access; the retry succeeded and the error was unrelated to
  this change.
- Docs: AGENTS.md and PLAN.md both described the ceiling as living *inside*
  the tiny-denominator carve-out, which is the exact coupling this phase
  removed. Both corrected.

### Phase 2: LLM extracts tiered requirements, code computes the score [x] DONE

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

**Outcome (observed, not predicted):**

- Prompt: schema is now `must_haves` / `nice_to_haves` / `decline_reason` /
  `ai_bonus_present`. The whole "Score rubric" section and the
  `score=0`-reserved-for-declines paragraph are gone. Added explicit tier
  definitions, a "never invent balance" rule (an empty tier-2 is normal), a
  "do not filter by whether the candidate has the skill" rule (omitting a
  missed requirement inflates the score), and a worked example.
- Code: `_phrase_credit` grades evidence (exact 1.0 / bridged 0.7 / none 0.0),
  `_TierResult` carries matched, gaps and graded credit, `_verify_tier`
  partitions one tier with a caller-owned `seen` set shared across tiers, and
  `_compute_score` does the arithmetic. `_coerce_score`,
  `_clamp_by_coverage`, `_coverage_pct` and `_verify_against_profile` were
  deleted rather than left dead.
- Discovered mid-phase: declines must be exempt from the
  no-requirements-extracted error. The model may legitimately stop reading
  once a title disqualifies a role, and raising there would mean the decline
  never persists and the job re-scores on every scan forever. Declines are
  filtered by `decline_reason`, not by score, so their number is cosmetic.
- Tests: `tests/test_score_compute.py` is new and pins the calibration table
  as the contract. `test_score_clamp.py` was ported, not re-baselined — three
  of its tests had been asserting against the thin `_job()` fixture while
  claiming to test the coverage clamp, and one (`familiar_only_soft_band`)
  could not prove its cap fired because the computed score landed below the
  cap; it now uses two genuinely Familiar-only skills so the uncapped value
  (90) is well above the cap (58).
- One of my own assertions was wrong and got corrected rather than kept:
  `test_tier1_dominates_tier2` asserted a >= 20 point gap I had not computed.
  The real design gives 15 (missing one of two hard requirements costs 25,
  missing the sole wish-list item costs 10). Now pinned to the true values.
- `pytest -q`: 1064 passed. `ruff` and `mypy` clean.

**Live re-score, 17 postings across the old bands (real Ollama):**

Scores now span 30-88 across 13 distinct values, versus six integers covering
136 of 169 before. The ordering inverted in the intended direction:

```
old  new   chars   tier1
 57   80    8015   6/8     Senior Software Engineer      <- full JD rises
 82   73   12530   9/21    faire Product Engineer
 82   50     500   1/3     Software Engineer             <- snippet falls
 62   35     500   0/2     AI/ML Engineer
 58   32   20068   1/10    Observability Architect       <- declined
 42   35   15573   0/22    Senior Network Engineering    <- declined
```

**Follow-up found, not fixed (out of Phase 2 scope):** an LLM-emitted
Familiar-only decline on a *senior* title is preserved without ever being
checked against `_all_matched_are_familiar`. Observed live: a 6228-char
posting scored 88 with 6/8 matched, carrying a Familiar-only decline the
deterministic check would have rejected. Pre-existing behaviour, not a Phase 2
regression, and harmless to selection (declines are filtered out regardless),
but the score/decline pair reads as contradictory. Logged below.

### Phase 3: weights in config, folded into prompt_hash [x] DONE

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

**Outcome (observed, not predicted):**

- `[pipeline]` gained the five `score_*` knobs with the planned defaults.
- New `score.ScoreWeights` frozen dataclass, resolved once per `score_job` via
  `from_config` and threaded explicitly through `_phrase_credit`,
  `_verify_tier` and `_compute_score`. Deliberately not read from module
  globals: a score should be reproducible from its inputs alone, and tests can
  vary one coefficient without monkeypatching module state. The `SCORE_*`
  constants stay as the defaults' mirror and as the `ScoreWeights()` fallback,
  with `test_config_defaults_match_the_module_constants` pinning them together
  so the two definitions cannot drift.
- **Signature change:** `prompt_hash(kb_dir)` became `prompt_hash(cfg)`. An
  optional `weights=` parameter was considered and rejected — a call site that
  forgot it would produce a hash that did not move on a weight change, leaving
  old-coefficient scores mixed into the queue and indistinguishable from new
  ones. Taking the whole `Config` makes that impossible. Both call sites
  (`scan_cmd`, `apply_cmd`) already had `cfg`.
- The float is serialized as `{:.6f}` so a `0.7` -> `0.70` reformat cannot
  change the digest on its own.
- Verified the hash tests fail without the change: stubbing `prompt_hash` to
  use `ScoreWeights()` instead of the config fails exactly the five
  `test_every_weight_moves_the_hash` cases and nothing else. A companion test
  pins that an unrelated knob (`cover_max_words`) does NOT move the hash, so
  the coverage is not just "everything invalidates everything".
- `pytest -q`: 1075 passed, up from 1064. `ruff` clean, `mypy` clean across all
  80 source files.
- Live check against the real `config.toml`:
  `ScoreWeights(base=30, tier1=50, tier2=10, ai_bonus=5,
  transferable_credit=0.7)`, `prompt_hash` `6547fe1cf5d99577`, and simulating
  tier1 50 -> 55 moves it to `d6d0b31ff400b2ae`.

### Phase 4: persist the score breakdown for outcome calibration [x] DONE

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

**Outcome (observed, not predicted):**

- `migrations/0010_score_breakdown.sql` adds one nullable TEXT column. A test
  asserts the file contains `ALTER TABLE SCORES ADD COLUMN` and none of
  DROP/DELETE/UPDATE/TRUNCATE, so it cannot become destructive later.
- `ScoreBreakdown` frozen dataclass records per-tier matched/total/credit, the
  AI bonus, pre-cap `computed`, post-cap `final`, `caps_applied`, and the
  weights in force. `caps_applied` is appended only when a ceiling actually
  lowers the score, so a cap that did not bind is not recorded as one.
- `ScoreResult.breakdown` is defaulted + last, so `apply_cmd._load_score`
  rebuilding from DB columns stays valid and reads None.

**Two bugs this phase would have shipped, found by testing rather than
reasoning, both now fixed and pinned:**

1. `apply` does not migrate on entry the way `scan` does, so a breakdown write
   against a DB not scanned since would fail hard with `table scores has no
   column named breakdown`. Reproduced against a synthetic pre-0010 database
   before fixing. `apply` now migrates (idempotent), and a test pins both the
   raw failure and the presence of the migrate call.
2. `calibrate` selected `s.breakdown` unconditionally, which raises `no such
   column` on a pre-0010 DB. It is read-only and must not migrate one out from
   under the user, so it now inspects `PRAGMA table_info` and selects
   `NULL AS breakdown` when the column is absent.

**Verification:**

- Migration applied to a **copy of the live database**: 255 score rows before
  and after, identical score sum (9258), column present, all 255 legacy rows
  NULL. Nothing rewritten.
- Real end-to-end score wrote and read back valid breakdown JSON.
- The phase's premise, confirmed on live data: three separate postings all
  land at **final=70** while having been computed **86, 90 and 90** at
  **92%, 100% and 100%** tier-1 coverage, all flattened by `caps_applied:
  ["thin_jd"]`. Calibrating against the score column would have been
  calibrating against the ceiling.
- `_tier1_coverage` returns None (not 0.0) for eight malformed/absent shapes,
  so unmeasured rows cannot drag the bottom coverage band down.
- One of my own test assertions was wrong and corrected: I asserted a 3.0/5
  coverage row lands in `< 60%`, but bands are `[lo, hi)` so 0.60 is the
  bottom of `60-74%`. The code was right.
- `pytest -q`: **1098 passed**, up from 1075. `ruff` clean, `mypy` clean.

**Not run:** the live `data/jobhunt.db` has NOT been migrated — only a copy
was. It migrates automatically on the next `jobhunt scan` or `apply`.

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

## Context window: 16k trialled, reverted to 32k; KV cache q4_0 -> q8_0 -> q4_0 (2026-07-28) [x] DONE

Not part of the scoring plan; requested mid-Phase-2 and done as its own change.
Net result: **`num_ctx` and `MAX_DESC_CHARS` are unchanged at 32768 / 16000**,
and the Ollama KV cache moved q4_0 -> q8_0.

**Why 16k was rejected.** Lowering `num_ctx` to 16384 forces `MAX_DESC_CHARS`
down with it, because the pairing must be measured, not estimated: real
`prompt_eval_count` runs **~23% above a chars/4 guess** on dense JD text. At
the 16000-char cap the tailor prompt measures **11886 tokens**, so with
`num_predict=4096` it needs 15982 of 16384 — **402 tokens of headroom**, and
the tailor RETRY appends a revisions block, i.e. it grows exactly when things
are already failing. Overflow is silent: Ollama truncates, the schema
instruction falls off the end, the model emits prose. Dropping the cap to
10000 restores headroom but truncates **19% of the 255-job backlog instead of
9%**, and a trailing "Preferred qualifications" block is exactly the tier-2
signal Phase 2 started weighting. Not worth it.

**KV cache q4_0 -> q8_0.** AGENTS.md had flagged q4_0 (set 2026-07-27 to save
VRAM) as a less-exercised path to revert "if CUDA illegal-memory-access faults
appear during scoring". They appeared twice on 2026-07-28, both mid-score,
`CUDA error: an illegal memory access was encountered` as an HTTP 500, with the
immediate retry succeeding each time. Intermittent, so a full backlog scan would
hit it repeatedly. Casey applied the systemd change.

**Measured consequence, which is a real trade:** at 32768 with q8_0 the model no
longer fits entirely in VRAM — `ollama ps` reports **6.9 GB, 14%/86% CPU/GPU**
(it was 5.7 GB / 100% GPU with q4_0). The cost is nominal in practice: three
consecutive warm score calls on the 12,530-char faire posting each took **5s**
and returned an identical 66, so the spill is not on the hot path and
temperature-0 reproducibility holds. Full `apply --no-browser` on
`adzuna_ca:5813760467` ran the tailor worst case end to end:
`verdict=ship keyword_coverage=100% missing=0 cover_violations=0`.

Two tests were updated rather than re-baselined during the 16k trial and kept
after the revert, because they were fragile either way:
`test_jd_context_caps.py` pinned `MAX_DESC_CHARS == 16000` as a literal and
sized a fixture JD against it. Its real contract is that prep tracks the
scoring budget and clears the old 6000 floor, so it now asserts the binding and
derives the fixture from the live budget instead of a number that goes stale on
every context change.

**Follow-up, same day: q8_0 -> q4_0 again, and `OLLAMA_KEEP_ALIVE` 30m -> 10m.**
The q8_0 CPU spill measured above (6.9 GB, 14%/86%) was judged the worse trade
of the two, so the KV cache is back to **q4_0** — ~288 MiB instead of ~576 MiB
at `num_ctx=32768`, and the model is 100% GPU-resident again at ~5.7 GB. The
intermittent CUDA illegal-memory-access fault documented above is a known,
accepted caveat of that path, not a solved problem: the gateway's retry absorbs
it, and q8_0 stays the fallback if it starts stalling full scans. The idle
unload also tightened from 30m to 10m so a shared box reclaims VRAM sooner;
it does not touch an active run, where the per-call `keep_alive=-1` wins.
Current systemd env is recorded in [AGENTS.md](AGENTS.md) Hardware context.
`num_ctx` and `MAX_DESC_CHARS` are still 32768 / 16000 — unchanged by any of this.

## Model comparison (run 2026-07-28, informs nothing in this plan yet)

Extraction quality is the load-bearing input now, so the model matters more
than it did when the LLM only had to pick a band. Measured on an RTX 3080
(10 GB VRAM), so both larger models spill to CPU. The user has confirmed a
speed hit is acceptable if quality justifies it.

Same faire posting (12,530 chars) through all three:

```
model                       time  score  tier1   notes
qwen3.5:9b                   11s     74   9/18   some spurious annotations
gemma4:26b-a4b-it-qat        39s     76   9/15   cleanest tier discipline
qwen3.6:35b-a3b-mtp-q4_K_M   38s     56  12/30   best matching, worst filtering
```

And on a clear-decline posting (Bazel/Go staff role, 7,991 chars):

```
qwen3.5:9b                    5s     40   1/10   declined correctly
gemma4:26b-a4b-it-qat        30s     43   1/11   declined correctly
qwen3.6:35b-a3b-mtp-q4_K_M   44s     55    2/9   MISSED the decline; but was
                                                 the only model to correctly
                                                 credit verified AWS
```

Reading: qwen3.6:35b matches individual skills best (it alone caught AWS) but
has the worst instruction-following on the "skip generic asks" rule, pulling
in `Problem-solving and collaboration skills`, `Product instincts`, and
`Fast-moving, cross-functional environments` as requirements. Under a
coverage-based score, denominator discipline outweighs match recall, so its
extra matches cost it 18 points. gemma4:26b has the best discipline (15
requirements, no generic asks) for roughly 3.5x the wall time.

Conclusion for now: **stay on qwen3.5:9b**, because the gap to gemma4 is 2
points on a single posting, i.e. inside the noise. The higher-leverage lever
is the prompt's generic-ask rule, which qwen3.6 demonstrates the cost of
ignoring. Revisit after Phase 3 makes weights tunable, and judge any model
swap on a fixed set of ~10 postings rather than one. Two postings is not a
basis for a switch.

## Deferred / follow-up

- **Unchecked Familiar-only decline on senior titles.** When the model itself
  emits a Familiar-only `decline_reason` and the title is senior-band, the
  deterministic layer neither nullifies it nor caps the score, so a posting
  can carry a high score alongside a Familiar-only decline. The junior path
  already nullifies these. Pre-existing, harmless to selection, but the pair
  reads as contradictory. Fix: also nullify when
  `_all_matched_are_familiar` is False.

- **Per-attempt progress output in the tailor retry loop.** Three failing
  attempts produce up to three minutes of silence followed by one error line,
  which reads as a hang. Recorded in PLAN.md under honesty enforcement item 3
  as a known gap. Lives in `apply_cmd`.

---

## Current work: surface-form false gaps in `phrase_present` (2026-08-29)

**Request restated:** review where the tailor/score gaps actually are, then
close the ones that are pipeline artifacts rather than real skill gaps.

## Evidence (682 live scores, 829 jobs)

Backlog gap counts for phrases whose skill IS verified Core:

```
ci/cd            13 gaps  (5 matched elsewhere)
ci/cd pipelines   9 gaps  (2 matched elsewhere)
react.js          7 gaps  (3 matched elsewhere)
reactjs           5 gaps  (2 matched elsewhere)
```

Reproduced directly against the current profile blob:

```
React      -> True      CI/CD            -> True
React.js   -> False     CI/CD pipelines  -> False
ReactJS    -> False
```

Two independent causes:

1. `React.js` tokenizes to the single token `react.js` (`_TOKEN_RE` keeps
   `.`), which is not a substring of a blob containing `react`. `ReactJS`
   likewise yields `reactjs`. The `.js` / `js` suffix is a surface form of one
   technology, not a different one.
2. `CI/CD pipelines` yields tokens `ci/cd` + `pipelines`. `pipelines` is
   absent from any resume, so `_all_tokens_present` fails. The whole-phrase
   '/'-split then gives `ci` (below `_MIN_ALT_LEN`) and `cd pipelines`
   (absent), so that path fails too. The blocker is a generic trailing noun
   carrying no technical discrimination — the same class `_STOPWORDS` already
   handles for `experience` / `skills` / `knowledge`.

## Phases

### Phase 1: `.js` surface-form equivalence + generic trailing nouns [x] DONE
- Status: complete. `pytest -q` 1121 passed, ruff + mypy --strict clean.
  Measured on the live backlog: 12 gap phrases across 12 jobs newly verify
  (`react.js` 7, `reactjs` 5) and nothing else changed — the delta was diffed
  phrase-by-phrase against the pre-change implementation to confirm no
  collateral widening.
- Scope correction during the phase: the first cut added "tools", "tooling",
  "technologies", "frameworks", "workflows", "practices", "principles",
  "standards" to `_STOPWORDS` and broke
  `test_verify_demotes_llm_matched_when_not_in_profile` (the Pigment
  regression: "AI/LLM tools" scored as matched against a profile that never
  claimed it). Those words are what make a vague ask vague. Narrowed to
  "pipeline"/"pipelines", the only pair with direct backlog evidence.
- Not swept into this diff: `_keywords.py` already failed
  `ruff format --check` at HEAD (PEER_FAMILIES and _STOPWORDS are
  hand-formatted compact). Running the formatter produced a 143-line
  cosmetic diff, which was reverted per the "do not rewrite working code
  purely to conform" rule. Worth its own phase if it should be settled.
- Files to touch: `src/jobhunt/pipeline/_keywords.py`,
  `tests/test_keywords_matching.py`
- Functions to add or change: `_token_present` (add `_surface_variants`),
  `_STOPWORDS` (extend)
- Reuse audit: `_strip_parenthetical` and the '/'-alternative path already
  exist and handle the other two surface-form classes; neither reaches these
  two cases. `PEER_FAMILIES` holds `node.js`/`nodejs`/`node` as peers but
  peer matching is only consulted by the audit fallback on short JDs, never by
  `phrase_present`, so it cannot close this.
- Simplest approach considered: add `react.js`/`reactjs` to PEER_FAMILIES.
  Rejected — it fixes React only, leaves every other `X.js` library broken,
  and abuses a table whose documented meaning is "peer technologies you could
  substitute", not "spellings of one technology".
- Scenarios (written from the requirement, before any code):
  - `React.js` / `ReactJS` / `react.js` match a blob containing `react`
  - `React` matches a blob containing only `react.js`
  - `CI/CD pipelines` matches a blob containing `github actions ci/cd`
  - `CI/CD` still does NOT match `we use circleci and a cdn` (existing test)
  - Genuinely-absent skills stay absent: Terraform, GraphQL/WPGraphQL,
    Kubernetes (existing tests)
  - A generic noun alone (`pipelines`) never matches on its own
- Verification: `pytest tests/test_keywords_matching.py tests/test_audit.py -q`,
  then full `pytest -q`, then re-measure the four gap counts above.
- Deferred out of this phase: denominator pollution (28% of extracted phrases
  are prose asks), the stale prompt_hash re-score, the profile-drift decision.

### Phase 2: drop pure-tenure asks from the tier denominator [x] DONE
- Status: complete. `pytest -q` 1121 passed + 12 new, ruff + mypy --strict clean.
- Files touched: `src/jobhunt/pipeline/score.py`, `tests/test_score_clamp.py`
- Functions added: `_is_pure_tenure_ask`, `_drop_pure_tenure_asks`, applied to
  both tier lists in `score_job` after the "model extracted nothing" check.
- Problem: "7+ years of professional software engineering experience" is
  extracted as a requirement and can never be satisfied by a resume keyword,
  so it is a permanent denominator miss. The YoE auto-decline rule already
  consumes the same requirement from `applicant.years_experience`, so the
  tenure bar was counted twice — once as a decline input, once as drag on
  coverage — against exactly the senior-leaning postings it had already judged.
- Precision check before wiring: over all 3,017 distinct extracted phrases the
  predicate drops 46, every one a bare tenure statement, and keeps all 91
  other phrases that name a year count because each carries a real qualifier
  ("2+ years leading cross-functional technical initiatives").
- Measured effect, replaying stored breakdowns with gaps split by tier:
  44 jobs shrink their denominator, pre-cap gain median +5 / max +30. Two
  postings cross `min_score` (50 -> 60, both Senior React/Three.js roles).
- **Honest limit:** most of the big pre-cap gains land on senior-titled
  postings, which `senior_score_cap=60` clamps straight back to 60, so the
  gain shows up in `computed`, not in `final`. That still matters — AGENTS.md
  requires `computed` and `final` stay distinct precisely so calibration has
  an uncapped number to work from — but it is not a ranking change for those
  rows. The rows it genuinely moves are the non-senior ones and the two
  promotions above.
- Deferred: the non-engineering title leak (161 jobs with >=3 prose asks, 0 of
  them caught by `is_non_engineering_title` / `is_management_title` — "Vehicle
  Inspector", "Car Detailer", "Chief of Staff", "Sales Executive" all pass).
  These score 30-43 and never reach the queue, so the cost is wasted LLM
  scoring passes, not bad ranking. Separate phase.

### Phase 3: Familiar decline is impossible against an empty bucket [x] DONE
- Status: complete. `pytest -q` 1138 passed, ruff + mypy --strict clean.
- Files touched: `src/jobhunt/pipeline/score.py`, `kb/prompts/score.md`
- Trigger: found by RUNNING the pipeline, not by reading it. A 12-job probe
  declined 4 (33%) with `"role's matched skills are all Familiar (academic/
  light use); not Core production experience"` — the prompt's string, not the
  code's, so the model was emitting it. With `skills_familiar` now `[]` the
  rule's premise cannot hold, and the existing nullification exempted senior
  titles, which is exactly where the false declines landed: Senior Generative
  AI Software Engineer, Senior Full-Stack Developer, Senior Java Full Stack
  Developer, Technical Lead (Java/AWS/Full Stack).
- Fix, both halves:
  - `score.py`: nullify any Familiar decline when `skills_familiar` is empty,
    regardless of title. Keyed on the bucket being empty rather than on the
    feature being removed, so reintroducing a Familiar tier restores the old
    behaviour with no further edit.
  - `score.md`: the rule now states that an empty list means the rule cannot
    apply. Removing it outright was considered and rejected — the tier may
    come back, and the deterministic post-filter still enforces the senior
    decline and junior ceiling when the bucket is populated.
- Verified on the four affected job ids: one now declines for a legitimate
  reason (`4+ tier-1 requirements the candidate cannot satisfy`, score 35),
  the other three clear at 60 with no decline.
- **Changes `prompt_hash` (98232fa2eb1d8110 -> 64415ae8e88a74cf)**, so this
  had to land before the backlog re-score, not after.

### Phase 4: backlog re-score [in progress]
- `jobhunt scan --skip-ingest --max-age-days 0`, 829 jobs, started 2026-08-29.
- `--skip-ingest` on purpose: no network ingest, no auto-discovery, so
  `config.toml` is untouched and the run is purely a re-score.
- Unblocked by the KV-cache change below.

## Environment note: q4_0 KV cache stalled the backlog (2026-08-29)

`OLLAMA_KV_CACHE_TYPE=q4_0` produced `CUDA error: an illegal memory access was
encountered` (HTTP 500 from /api/chat) on **2 of 5** score calls, not the
"intermittent" rate AGENTS.md recorded from 2026-07-28. Casey switched the
systemd override to `q8_0` (and `OLLAMA_KEEP_ALIVE` 10m -> 0); a 12-job probe
then scored **12/12** with zero faults. Cost: ~13.7 s/job vs ~8 s/job, which
is the documented CPU/GPU split, and it completes.

**AGENTS.md is wrong on one point.** It says those faults were "each recovered
by the gateway's immediate retry". They are not recoverable: `client._post`
raises `GatewayError` on any status >= 400, and the only retry in
`complete_json` covers invalid JSON, not HTTP 500. A CUDA fault is a hard
per-job skip. Either add a 500-retry or correct the claim.
