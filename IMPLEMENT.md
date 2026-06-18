# IMPLEMENT.md: Execution engine

Pillar 4 of the documentation architecture defined in [AGENTS.md](AGENTS.md).
This file is the granular, phase-by-phase breakdown of in-flight work. Agents
**read** this at the start of every phase (Phase 1 and Phase 3 of the Workflow
Contract) and **write** to it as part of the Definition of Done, checking off
completed phases and logging deferred work as new phases. Never leave a plan in
conversational context alone; it lives here.

## Phase template

Copy this block for each phase of an approved plan. One phase = one atomic,
revertable commit (see Phase-Sizing Rules in `AGENTS.md`).

```
### Phase <N> — <one-sentence goal, no "and">

**Goal:** <single declarative sentence>

**Files to touch:**
- path/to/file — what changes

**Functions to add/change:**
- module.function — add | change — what it does

**Reuse audit:** (per Reuse-First Rule in AGENTS.md)
- Search terms: `<grep/rg terms used>`
- Candidates found: <list, or "none">
- Why not reused: <reason per candidate>

**Verification:** (≤ 3 bullets)
- <how to prove it works — test name or manual E2E output>

**Status:** [ ] not started | [ ] in progress | [ ] done
```

## In-flight: interview-prep honesty hardening (2026-06-12)

Scope: fix the defects surfaced by the Urban Customz prep run
(`manual:615a73a99cb2`, doc generated 2026-06-12). Observed failures, each
traced to a root cause in `pipeline/interview_prep.py`:

- Comp heads-up parsed "$18-$19 an hour" as **USD/year** and suggested the
  "your range looks in line" phrasing against a JD paying roughly half of
  Casey's stated floor. Root cause: `_SALARY_RE` only accepts `per hour|hr`
  as a unit, then `extract_comp_section` defaults unit to `year` and
  currency to `USD`, and the suggested phrasing never compares the two
  ranges.
- Likely-question beats claimed JD-named tech Casey has never used
  ("applied similar tactics to Dawn... CDN edge caching", "explored
  Hydrogen", "followed Shopify Flow documentation"). Root cause: the
  JD-mirror check runs off `_CLAIM_JD_PHRASES`, a hardcoded list lifted
  from one past SEO-agency JD, so phrases from any other JD can never fire.
- A beat positively claimed Hydrogen while the Honest Gaps section
  correctly declared it a gap. No check compares claims against the doc's
  own gap declarations.
- A beat inverted a verified fact (claimed Casey "built custom Liquid
  configurator sections instead of generic apps" where verified history
  says he integrated a third-party ring-builder app) and a gap reframe
  misattributed Next.js/React to the Python-only Jobhunt project. These
  two classes are not deterministically checkable, so they are addressed
  at the prompt level, not the validator.

Decisions made (correct me if wrong):

- When a JD names no currency, default to **CAD**, not USD. This tool is
  GTA-scoped, unstated ranges are almost always CAD, and the current USD
  default inflates the CAD estimate by 1.37x.
- When a JD names no pay unit, infer hourly when the high bound is under
  1000, else annual. A "$19/year" parse is always wrong.
- The new gap-contradiction check (Phase 3) is **blocking**, since it is
  high-precision by construction. The dynamic JD-mirror check (Phase 4)
  starts **non-blocking** (warning + retry hint only) until its
  false-positive rate is observed on real runs. Both feed the existing
  retry loop either way, because `draft_prep_with_retry` retries on any
  violation.
- `_CLAIM_JD_PHRASES` stays as a curated supplement, the dynamic
  derivation augments rather than replaces it.

**Reuse audit:** (per Reuse-First Rule)

- Search terms: `rg "salary|hourly|per hour" src/`, `rg "_SALARY_RE"`,
  `rg "_NEGATION_PRECEDES_RE|_FABRICATION_WATCHLIST" src/`,
  `rg "phrase_present|PEER_FAMILIES" src/`, `rg "_numbers_from_text"`,
  `rg "_has_verified_trace|_substantive_tokens"`.
- Candidates found: `_SALARY_RE` + `extract_comp_section` (interview_prep,
  the code under repair), `_numbers_from_text` (reused in Phase 2 to parse
  the applicant range), `_NEGATION_PRECEDES_RE` from `cover_validate`
  (reused in Phases 3-4 for negation suppression, already imported),
  `phrase_present` from `pipeline._keywords` (reused in Phase 4 for
  word-boundary token matching), `_has_verified_trace` /
  `_substantive_tokens` (reused in Phase 3 for blob membership),
  `_FABRICATION_WATCHLIST` (cover-shared static list, deliberately not
  extended here, see deferred Phase 6).
- Why not reused elsewhere: no existing helper compares two salary ranges
  or derives JD-only tech tokens, those are new functions listed per phase.

### Phase 1: Parse hourly and currencyless JD pay ranges correctly

**Goal:** `extract_comp_section` renders "an hour"-style and
currencyless JD ranges with the right unit and currency.

**Files to touch:**
- src/jobhunt/pipeline/interview_prep.py — `_SALARY_RE`,
  `extract_comp_section`
- tests/test_interview_prep.py — new comp-extraction cases

**Functions to add/change:**
- `_SALARY_RE` — change — accept `an hour` / `a year` / `/hour` / `/yr` /
  `hourly` / `annually` unit forms
- `extract_comp_section` — change — CAD default when currency unstated,
  hourly inference when unit unstated and high bound < 1000

**Verification:**
- New unit test: "$18.00-$19.00 per hour" and "$18-$19 an hour" both render
  hourly CAD with ~$37k-$40k annualization, no USD anywhere.
- New unit test: "$90,000 - $110,000" (no unit, no currency) renders annual
  CAD with no conversion line.
- `uv run pytest -q tests/test_interview_prep.py` green.

**Status:** [x] done (2026-06-12). Full suite 885 passed, mypy clean, ruff
error count unchanged from HEAD (16 pre-existing E501s in untouched lines).
One extra in-surface edit: the mislabeled `test_comp_section_usd_hourly`
(its JD names no currency) was renamed to
`test_comp_section_hourly_defaults_cad` with a `USD not in out` assertion.
Hourly CAD ranges render a single annualized line instead of the duplicate
conversion parenthetical.

### Phase 2: Make the suggested comp phrasing range-aware

**Goal:** The suggested recruiter phrasing reflects how the JD's
annualized CAD range actually compares to the applicant's stated range.

**Files to touch:**
- src/jobhunt/pipeline/interview_prep.py — `extract_comp_section` + one new
  helper
- tests/test_interview_prep.py — phrasing-selection cases

**Functions to add/change:**
- `_range_fit` — add — compares (cad_low, cad_high) against the parsed
  applicant range (via the existing `_numbers_from_text`), returns
  below / overlaps / above
- `extract_comp_section` — change — three phrasing variants keyed on
  `_range_fit`, the below-range variant warns instead of saying "in line"

**Verification:**
- Unit test: the Urban Customz numbers ($18-19/hr vs "60,000 - 90,000")
  select the below-range warning phrasing.
- Unit test: an overlapping annual range still selects the current
  "in line" phrasing verbatim.

**Status:** [x] done (2026-06-12). Full suite 890 passed, mypy clean, ruff
unchanged from HEAD. Planned-reuse deviation: `_numbers_from_text` returns
an unordered set (allowlist-shaped), so `_range_fit` does its own ordered
amount parse via a new module-level `_AMOUNT_RE` (handles commas and a `k`
suffix). An unparseable applicant range returns None and keeps the neutral
"in line" phrasing. An above-range variant was added as planned.

### Phase 3: Reject claims that contradict the doc's own honest gaps

**Goal:** A likely-question beat or anchor that positively claims a tech
named in an honest gap (and absent from the verified blob) is a blocking
violation.

**Files to touch:**
- src/jobhunt/pipeline/interview_prep.py — `validate_prep_sections`,
  `has_blocking_prep_violations`, `_format_revision_hint`, one new helper
- tests/test_interview_prep.py — contradiction cases

**Functions to add/change:**
- `_gap_contradiction_violations` — add — extracts substantive tokens from
  each `honest_gaps[].gap` that do not trace to the verified blob, then
  flags any anchor or beat containing such a token outside a
  `_NEGATION_PRECEDES_RE` context
- `validate_prep_sections` — change — call the new helper
- `has_blocking_prep_violations` — change — add the new needle
- `_format_revision_hint` — change — hint text telling the model to keep
  gap techs out of positive claims

**Verification:**
- Unit test: gap "never shipped Hydrogen" + beat "preferred Hydrogen
  patterns" yields a blocking violation, while reframe "I haven't used
  Hydrogen, but..." stays clean (negation suppression).
- Unit test: gap naming a verified-blob skill produces no violation.
- Full `uv run pytest -q` green.

**Status:** [ ] not started

### Phase 4: Derive JD-mirror claim phrases from the JD instead of a static list

**Goal:** Casey-claim bullets that assert ownership of tech tokens present
in the JD but absent from the verified blob produce a (non-blocking)
violation for any JD, not just the one hardcoded in `_CLAIM_JD_PHRASES`.

**Files to touch:**
- src/jobhunt/pipeline/interview_prep.py — new token derivation + claim
  framing detection, wired into the existing mirror loop in
  `validate_prep_sections`
- tests/test_interview_prep.py — derivation + framing cases

**Functions to add/change:**
- `_jd_only_tech_tokens` — add — capitalized or tech-shaped tokens from the
  JD (word-boundary matched via `pipeline._keywords.phrase_present`) that do
  not trace to the verified blob, stop-word filtered
- `_CLAIM_VERB_RE` — add — ownership framing (built / used / applied /
  implemented / shipped / explored / leveraged / preferred / tested with)
  required in the same bullet before the token fires
- `validate_prep_sections` — change — run the dynamic tokens through the
  same per-bullet loop as `_CLAIM_JD_PHRASES`, emitting
  `casey claim mirrors unverified JD tech` (not added to blocking needles)

**Verification:**
- Unit test against the Urban Customz JD + the shipped doc's beats: Dawn,
  Hydrogen, and Shopify Flow claims all fire, the verified-anchor beats do
  not.
- Unit test: JD context lines in role_decode / questions_to_ask never fire
  (claim sections only).
- Full `uv run pytest -q` green.

**Status:** [ ] not started

### Phase 5: Harden the interview-prep prompt's beat discipline

**Goal:** The prompt forbids the confabulation classes the validator
cannot catch deterministically.

**Files to touch:**
- kb/prompts/interview-prep.md — beats and reframes rules

**Changes:**
- Beats may either restate a verified work-history bullet (paraphrase
  allowed, no new specifics) or give method-voice advice ("walk through
  console errors, then..."), never invent past-tense actions or metrics.
- Reframes must attribute skills to the project or role they actually
  belong to in the verified facts, never re-anchor a skill onto a
  different project.
- Claims must not contradict the honest-gaps section of the same doc.

**Verification:**
- Manual E2E (not CI, per testing rules): re-run
  `jobhunt interview-prep manual:615a73a99cb2 --stage hiring_manager` and
  confirm the regenerated doc has none of the five observed failure
  classes, with attempts and violation counts reported.

**Status:** [ ] not started

### Phase 6 (deferred, needs sign-off): Shopify-ecosystem watchlist entries

**Goal:** Add Hydrogen / Shopify Plus / Shopify Flow to
`cover_validate._FABRICATION_WATCHLIST` so the cover pipeline gains the
same protection.

Deferred because the watchlist is shared with the cover pipeline and its
false-positive rate there needs Casey's judgment (`analyze validators` is
the tuning tool). Phase 4's dynamic check covers interview-prep without
this.

**Status:** [ ] not started

### Phase 7 (deferred, trivial tier): Skeleton checklist accuracy

**Goal:** The pre-call checklist only mentions "the tailored resume" when
a tailored artifact exists for the job under `data/applications/`.

**Status:** [ ] not started

## In-flight: Doc sanitization + cross-doc alignment (2026-06-12)

Scope: bring README.md, PLAN.md, kb/README.md, and this file's preamble into
factual alignment with AGENTS.md and the code, and apply the AGENTS.md
documentation-style rules (no em/en dashes, no semicolons in prose) to those
docs. AGENTS.md itself is out of scope for style (its own rule grandfathers
existing content) and was verified as factually current against the code, so
it is the alignment baseline. Verified ground truths used below: config.py
gateway tasks include an `answer` slot, `ingest/_filter.GTA_CITIES` has 22
cities (Toronto + 16 GTA + 4 KW-corridor + Barrie), kb/profile/ contains
`projects.md`, migrations run 0001-0008.

**Reuse audit:** not applicable. Markdown-only changes, no code, no new
functions. Searches run during planning: `grep -n "base_url\|tasks" config.py`,
`grep -n GTA_CITIES ingest/_filter.py`, `ls migrations/ kb/profile/`, and
per-file `grep -c` for `—`, `–`, `;`.

### Phase 1: Align PLAN.md facts with AGENTS.md and the code

**Goal:** Fix the factual drift in PLAN.md so it matches the current code.

**Files to touch:**
- PLAN.md: facts only, no style edits

**Changes:**
- "All three task slots (score, tailor, cover)" → four slots, adding `answer`
  (config.py defines score/tailor/cover/answer/embed). Same fix in the
  Models table row.
- Honesty section says "enforced in five places" but lists six items → six.
- Sources section and success criteria omit Workable and Recruitee → add both
  (adapters exist, README and AGENTS.md list them).
- Filter-pipeline paragraph says "Toronto + 16 surrounding municipalities" →
  reword to match `GTA_CITIES` (Toronto plus 21 others, naming the KW corridor
  and Barrie).

**Verification:**
- `grep -n "three task slots\|five places" PLAN.md` returns nothing.
- PLAN.md source lists name Workable and Recruitee.

**Status:** [x] done (2026-06-12)

### Phase 2: Sanitize PLAN.md prose style

**Goal:** Remove em/en dashes and semicolons from PLAN.md prose per the
AGENTS.md documentation-style rules.

**Files to touch:**
- PLAN.md: prose recasts only, no meaning changes. Code spans and config
  literals stay untouched where punctuation is load-bearing. Date and numeric
  ranges become hyphens.

**Verification:**
- `grep -c '—\|–' PLAN.md` reports 0 in prose (any survivor is inside an
  inline-code span).
- Semicolons remain only inside code spans or shell/TOML comments.

**Status:** [x] done (2026-06-12). Two surviving em dashes are exempt
literals: the `Remote (on-call) — US` inline-code example and the
`Late — diminishing` verdict label emitted verbatim by
`analyze_cmd._classify_verdict`.

### Phase 3: Align README.md facts with AGENTS.md and the code

**Goal:** Fix the stale facts in README.md.

**Files to touch:**
- README.md: facts only. README prose is already dash-free, and all its
  semicolons sit in code blocks, so no style pass is needed.

**Changes:**
- Ollama systemd block is stale: `OLLAMA_KV_CACHE_TYPE=q4_0` → `q8_0`,
  `OLLAMA_KEEP_ALIVE=10m` → `-1` (AGENTS.md hardware context, 2026-06-04).
- `[gateway.tasks]` config example omits the `answer` slot → add
  `answer = "qwen3.5:9b"`.
- Run-on sentence in the intro ("...natively, slug curation is mostly
  automatic") → split.

**Verification:**
- README systemd block matches the AGENTS.md block verbatim.
- `grep -n 'answer.*qwen' README.md` hits the gateway.tasks example.

**Status:** [x] done (2026-06-12). The README systemd block matches the
AGENTS.md values (the AGENTS block's inline rationale comments are not
copied, since README states the rationale in prose).

### Phase 4: Align then sanitize kb/README.md

**Goal:** Bring kb/README.md up to date and into style compliance.

**Files to touch:**
- kb/README.md

**Changes:**
- Profile sidecar list omits `projects.md` (added in PB3, present on disk) →
  add it.
- Remove em dashes and semicolons from prose per the style rules.

**Verification:**
- `grep -c '—\|–\|;' kb/README.md` reports 0 outside code spans.
- Sidecar list matches `ls kb/profile/`.

**Status:** [x] done (2026-06-12)

### Phase 5: Sanitize this file's prose

**Goal:** Remove em dashes from IMPLEMENT.md's own preamble prose.

**Files to touch:**
- IMPLEMENT.md: preamble paragraphs plus this plan's own headings and
  bullets. The phase template lives in a code block and is exempt.

**Verification:**
- No `—` or `–` outside code blocks in IMPLEMENT.md.

**Status:** [x] done (2026-06-12). Scope grew slightly beyond "preamble
only": this plan's own phase headings and file bullets had dashes too, so
they were recast in the same pass. All five phases of the doc-sanitization
plan are complete.

## In-flight: personal/ files truth-sync (2026-06-12)

Scope: bring the five files in `personal/` in line with their stated purposes
and with the live LinkedIn profile Casey pasted on 2026-06-12. These files are
gitignored, so phases end without commits. Ground truths: `verified.json`
(regenerated 2026-06-10) holds the canonical skill buckets and role labels,
and Casey's 2026-06-11 decision made the de-confidentialed client labels
canonical ("SEO AI Marketing Agency", "Vintage Gaming Retailer", no
"(Confidential)" tags).

Live-paste findings driving the plan:
- REGRESSION: the LinkedIn Atelier Dacko entry shows the agency's HubSpot
  bullets duplicated verbatim, with the Shopify content missing.
- REGRESSION: Dacko employment type shows "Permanent Full-time" (decided:
  plain Contract).
- Still deferred: Top Skills and the Skills section (LinkedIn editor bug),
  Featured section, commerce project entry (needs the keystone build).
- Label drift: both client entries still say "(Confidential)", and the agency
  name on LinkedIn ("SEO AI Agency") differs from the resume's canonical
  "SEO AI Marketing Agency".
- Prep doc says "9 yrs" in §6 Strengths where every other reference says ten.
- The one-pager resume lacks the Contentful in-progress bullet under Dacko
  that LinkedIn copy and the prep doc both carry.
- Analytics deltas for the action plan: search appearances 17 to 22, profile
  views 17 to 20, 261 post impressions, connections flat at 80.

Decisions made (correct me if wrong):
- Canonical agency label everywhere is "SEO AI Marketing Agency" with no
  "(Confidential)" tag, per the 2026-06-11 decision and `verified.json`.
- "GitHub Actions CI/CD" stays in the resume skills row: it matches
  `verified.json` and is defensible via own projects. The client-claim
  guardrail (lint CI yes, auto-deploy never) is unchanged.
- Real Atelier Dacko metrics (Search Console growth, ring-builder orders)
  remain pending from Casey. No numbers are invented anywhere. The action
  plan keeps the nudge.

**Reuse audit:** not applicable. Content edits to gitignored personal docs
plus one python-docx edit. No new code, no new utilities.

### Phase P1: Make WORK.md the full-truth work-experience record

**Goal:** Expand WORK.md from a projects-and-education KB into the canonical
honest record of all four work engagements that resume and LinkedIn must
reflect.

**Files to touch:**
- personal/WORK.md: new "Work history" section (Atelier Dacko, SEO AI
  Marketing Agency, Vintage Gaming Retailer, culinary) carrying canonical
  labels, dates, employment types, defensible claims, in-progress flags, and
  banned phrasings from the D0 truth audit. Purpose paragraph and related-files
  list updated to match the wider role.

**Verification:**
- Every claim in the new section traces to a D0 verdict, the prep doc
  evidence bank, or verified.json. No new facts invented.
- Labels and dates match verified.json role tuples exactly.

**Status:** [x] done (2026-06-12). Discovery during the phase: a second,
newer WORK.md existed at the repo root. Casey chose personal/WORK.md as
canonical. The root copy's newer baseline-resume paragraph was merged in,
the work-history section was added unnumbered (tracked docs reference
"Section 1" = projects and "Section 2" = education by number), and the root
file became a pointer stub. The root copy turned out to be gitignored all
along, so no untracking was needed.

### Phase P2: Sync the one-pager resume with the decided claims

**Goal:** Add the missing Contentful in-progress bullet to
Casey_Hsu_Resume.docx and recast its one em-dash bullet.

**Files to touch:**
- personal/Casey_Hsu_Resume.docx (via python-docx, .bak copy first): add
  "Currently implementing Contentful to manage the brand's content (in
  progress)." under Atelier Dacko, and recast the agency perf bullet to the
  dash-free LinkedIn wording (an AI-tic flag per action-plan Phase C3).

**Verification:**
- Re-dump the docx text: new bullet present under Dacko, no em dash in the
  perf bullet, all other text byte-identical.
- Backup file exists beside the original.

**Status:** [x] done (2026-06-12)

### Phase P3: Rewrite LinkedIn_Updates.md against the live paste

**Goal:** Replace the stale "fix pass complete" state with a current
instruction list that leads with the two regressions.

**Files to touch:**
- personal/LinkedIn_Updates.md: regression fixes first (paste-ready Dacko
  Shopify bullets, employment type to Contract), then the label cleanup
  (drop "(Confidential)" from both clients, rename agency to "SEO AI
  Marketing Agency"), then the deferred bug items (Top Skills, Skills
  section), then the later items (Featured, keystone project, coursework).

**Verification:**
- Every instruction is checkable against the 2026-06-12 paste.
- Paste-ready blocks match WORK.md claims word for word where they overlap.

**Status:** [x] done (2026-06-12)

### Phase P4: Fix the prep doc's years drift

**Goal:** Correct the "(9 yrs)" Strengths line to ten years and re-check the
doc for any other stale numbers.

**Files to touch:**
- personal/Casey_Hsu_Interview_Prep_Master.md: §6 Strengths bullet, plus any
  other "9 yr" stragglers a grep pass finds.

**Verification:**
- `grep -n '9 yr\|nine year' personal/Casey_Hsu_Interview_Prep_Master.md`
  returns nothing.

**Status:** [x] done (2026-06-12). The §6 line was the only straggler.

### Phase P5: Harden Casey_Action_Plan.md into a v4 next-steps plan

**Goal:** Restructure the action plan so a dated, prioritized next-steps
queue leads and the completed audit material is compressed to a decisions
record.

**Files to touch:**
- personal/Casey_Action_Plan.md: new v4 header with a "next 7 days / next
  14 days" queue (LinkedIn regression fixes, Skills retry on mobile, first
  invite batch, HubSpot free cert, Liquid Storefronts exam booking, keystone
  build start, Dacko metrics + Contentful scope nudges). Status updates from
  the 2026-06-12 paste (analytics deltas, connections flat, post cadence
  working). Completed Parts D0-D7 compressed to a decisions-of-record table
  pointing at WORK.md as the canonical claims source. Parts A, B, C, E, F
  retained with statuses corrected.

**Verification:**
- No open item from v3 is silently dropped: each is either in the v4 queue,
  marked done, or explicitly parked with a reason.
- The queue is consistent with the job-by-end-of-June goal and the 60/40
  people-vs-applications split.

**Status:** [x] done (2026-06-12). One v3 item re-parked with a new reason:
the optional lint-CI resume bullet is parked because the one-pager is full;
it surfaces per-JD via the tailor instead. All phases P1-P5 complete.

## In-flight: Baseline_Resume.docx consistency merge (2026-06-12)

Casey asked to "replace Baseline_Resume.docx with the updated full version
for consistency". Inspection shows the baseline is already richer than the
full one-pager build (Familiar row, Figma/Astro in Core, six PROJECTS
entries with Stack: lines and narratives in the parser's expected format),
so a literal overwrite would lose content and break parsing. Interpretation
adopted: merge today's claim deltas INTO the baseline, then regenerate the
profile and verify the pipeline. Repo-URL fact verified via GitHub API:
SimBuds/Ollama-LLM-Prompts 301-redirects to SimBuds/Local-LLM, so Local-LLM
is canonical and WORK.md is stale.

**Reuse audit:** not applicable. Content edits via python-docx plus running
existing CLI commands (`jobhunt convert-resume`, pytest). No new code.

### Phase B1: Merge today's claim deltas into Baseline_Resume.docx

**Goal:** Bring the baseline's claims in line with the decided versions
without touching its structure.

**Files to touch:**
- Baseline_Resume.docx (backup copy first): add the lint-CI bullet to the
  agency role (D1 item, real and defensible), recast the em-dash perf bullet
  to the dash-free "Cut page load time 30%..." wording, add
  "editor-configurable" to the Figma bullet, and add the culinary overlap
  bullet ("Ran kitchens alongside a full-time programming diploma and first
  development contracts from 2023 to 2025.").

**Verification:**
- Text diff vs backup shows exactly the four planned changes.

**Status:** [x] done (2026-06-12)

### Phase B2: Regenerate the profile and verify the pipeline

**Goal:** Re-run convert-resume on the merged baseline and prove nothing
downstream breaks.

**Files to touch:**
- kb/profile/* (regenerated by `jobhunt convert-resume`, not hand-edited)

**Verification:**
- `jobhunt convert-resume` completes with zero parse warnings.
- verified.json diff shows only work-history bullet changes, no skill-bucket
  or role-tuple changes.
- `uv run pytest -q` green.

**Status:** [x] done (2026-06-12). 881 passed. verified.json diff: bullet
changes only on the agency and culinary roles, no skill-bucket or role-tuple
changes.

### Phase B3: Fix the staleness this exposed in downstream docs

**Goal:** Correct the project count and repo URL drift found during
inspection.

**Files to touch:**
- personal/WORK.md: "baseline carries four projects" is wrong (it carries
  all six); AI Context Stack repo is `github.com/SimBuds/Local-LLM` (the
  Ollama-LLM-Prompts name 301-redirects).
- personal/Casey_Hsu_Resume_Full.docx: same URL fix.
- Resume_Tailoring_Instructions.md: check its Section 2 project list for the
  same two staleness items; fix only if present.

**Verification:**
- grep finds no Ollama-LLM-Prompts reference outside git history.
- WORK.md project-count claim matches the baseline PROJECTS section.

**Status:** [x] done (2026-06-12). Two extra trivial-tier fixes in the same
entries while there: WORK.md's stale "off-resume" labels on macOS KVM and
the hybrid agent (all six are on the baseline now), and the tailoring
instructions' stale AI Context Stack model names (qwen3.5 build retired,
current = Qwen3.6 MoE + Gemma4 12B per WORK.md D6).

## In-flight: `db reset` covers interview-prep + answers (2026-06-13)

### Phase 1 — `db reset` also wipes `data/interview-prep/` and `data/answers/`

**Goal:** Add the interview-prep and standalone-answers output dirs to the
set `jobhunt db reset` removes.

**Files to touch:**
- src/jobhunt/commands/db_cmd.py — extract the reset target list into a pure
  `_reset_targets(cfg)` helper and add `data_dir / "interview-prep"` and
  `data_dir / "answers"`; refresh the `reset` docstring.
- tests/test_db_reset_targets.py — new unit test over `_reset_targets`.
- AGENTS.md — update the `db reset` description (Commands > Hidden internals).
- README.md — update the "start over" line (Onboarding) to list the two dirs.

**Functions to add/change:**
- db_cmd._reset_targets — add — pure `cfg -> list[Path]` builder for the
  reset target list (so the path set is unit-testable without CliRunner,
  filesystem, or the confirmation prompt).
- db_cmd.reset — change — call `_reset_targets(cfg)` instead of building the
  list inline; behavior identical except the two added dirs + docstring.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `rg -n "interview-prep|data/answers|\"answers\"" src/jobhunt`,
  `rg -rln "reset" tests/`, `rg -n "data_dir / " src/jobhunt/commands`.
- Candidates found: the two output-dir paths are constructed inline in
  `apply_cmd`/`interview_prep_cmd` (`data_dir / "interview-prep"`),
  `answer_cmd`/`_answer_index` (`data_dir / "answers"`); no existing
  shared "reset targets" or "data output dirs" helper; no existing reset
  test.
- Why not reused: the path strings are one-liners off `cfg.paths.data_dir`,
  not a shared constant — duplicating the two `data_dir / "..."` joins in the
  reset list matches the existing inline style and avoids a new cross-module
  dependency. Job-scoped answers (`data/applications/<id>/answers/`) are
  already removed via the existing `applications` target, so only the two
  top-level dirs are added.

**Verification:** (≤ 3 bullets)
- New test asserts `_reset_targets` includes the interview-prep and answers
  dirs and still includes applications + cache (fails before the change).
- `pytest -q tests/test_db_reset_targets.py` passes.

**Status:** [x] done (2026-06-13). `_reset_targets` extracted in
`db_cmd.py`, two dirs added, docstring refreshed; new
`tests/test_db_reset_targets.py` passes (12 db tests green); ruff +
`mypy --strict` clean; AGENTS.md and README.md descriptions updated.

## In-flight: work-experience + resume truth-sync (2026-06-18)

Scope: iron out every divergence between the three fact layers and bring them
back into one canonical state. A Jun 13 manual edit to `Baseline_Resume.docx`
removed the Familiar skills row, trimmed PROJECTS from 6 entries to 4, and
dropped the per-project `Stack:` lines, but `WORK.md` and
`Resume_Tailoring_Instructions.md` still describe the old state, and
`Resume_Tailoring_Instructions.md` (injected into the tailor prompt as
`{policy}`) still lists metrics WORK.md banned in its 2026-06-10 truth audit.

Decisions made by Casey (2026-06-18):
- **Restore the Familiar row** to the docx (Java, Spring Boot, Angular, MCP
  (Model Context Protocol) Servers, Agile/Scrum, Headless Architecture, Figma,
  Astro) and re-run convert-resume. Restores the honesty signal and the
  Familiar-only-fit score cap.
- **Keep 4 projects, add back Stack lines**, and fix the AI Context Stack repo
  URL from `Ollama-LLM-Prompts` to `Local-LLM` (the old name 301-redirects).
- **Full doc re-sync** across WORK.md, Resume_Tailoring_Instructions.md, and
  PLAN.md once the facts are locked.

Ground truths used: `WORK.md` work-history + Section 1 stacks are canonical;
`verified.json` is the machine source of truth regenerated from the docx;
`parse_docx` keys skill rows on an exact `Label:` (so `Familiar:` populates
`skills_familiar`), per-project stacks on a line starting `Stack:`, and project
urls on the hyperlink target.

Per-project Stack lines to add (verbatim from WORK.md Section 1):
- Jobhunt: `Python, uv, Ollama, SQLite, Playwright, public ATS APIs`
- Auto-Agent: `FastAPI, Claude API, Postgres, Redis, Docker, Arch Linux, D-Bus`
- AI Context Stack: `Ollama, Modelfile, Qwen3.6, Gemma4, layered Markdown`
- SEO-LLM: `Claude Code, Ollama, Postgres, JSON-LD`

**Reuse audit:** not applicable. Content edits to `Baseline_Resume.docx` (via
python-docx) plus markdown doc edits plus running the existing
`jobhunt convert-resume` CLI. No new code, no new functions. Mirrors the
2026-06-12 "Baseline_Resume.docx consistency merge" precedent (Phases B1-B3).

Open factual items (NOT blocking these phases, folded into deferred Phase 6
until Casey supplies them): Atelier Dacko Search Console growth + ring-builder
order counts, Contentful implementation scope wording, Stripe status. Also
non-blocking: confirm the canonical resume email (`casey-hsu@outlook.com` on
the resume vs the `caseyhsu@proton.me` account email) — left unchanged unless
Casey says otherwise.

### Phase 1 - Restore the Familiar row, project Stack lines, and fix the AI Context Stack URL in the docx

**Goal:** Bring `Baseline_Resume.docx` to the decided content state in one
co-located edit.

**Files to touch:**
- Baseline_Resume.docx (write a `.bak` copy first) - add a
  `Familiar: ...` paragraph to TECHNICAL SKILLS (placed last, after
  `Project Stack:`); add a `Stack: ...` paragraph under each of the 4 PROJECTS
  entries; change the AI Context Stack header url to
  `https://github.com/SimBuds/Local-LLM` (updating both visible text and the
  hyperlink relationship target so the parser reads the new url).

**Functions to add/change:** none (python-docx content edit only).

**Note on the one-sentence rule:** three edit kinds in one phase, justified
because they are co-located edits to a single file with a single downstream
regeneration. Splitting would force three convert-resume runs to verify
intermediate states with no atomic-revert benefit.

**Verification:** (<= 3 bullets)
- Re-dump docx text: a `Familiar:` row with the 8 items is present, each of the
  4 projects has a `Stack:` line, the AI Context Stack url reads `Local-LLM`,
  and all other paragraphs are byte-identical to the `.bak`.
- The `.bak` snapshot exists beside the original.

**Status:** [x] done (2026-06-18). `Baseline_Resume.docx.bak` written first.
Diff vs `.bak` shows only the 6 planned additions (1 Familiar row + 4 Stack
lines + the AI Context Stack Stack line) and the single AI Context Stack url
line changing `Ollama-LLM-Prompts` -> `Local-LLM`. Hyperlink relationship
target confirmed = `https://github.com/SimBuds/Local-LLM`. All other
paragraphs byte-identical.

### Phase 2 - Regenerate the profile and verify the pipeline

**Goal:** Re-run convert-resume on the edited baseline and prove nothing
downstream breaks.

**Files to touch:**
- kb/profile/* (regenerated by `jobhunt convert-resume`, not hand-edited).

**Functions to add/change:** none.

**Verification:** (<= 3 bullets)
- `jobhunt convert-resume` completes with zero parse warnings.
- verified.json diff shows only: `skills_familiar` populated with the 8 items,
  the 4 project `stack` arrays populated, and the AI Context Stack url changed
  to `github.com/SimBuds/Local-LLM`. No work-history or other skill-bucket
  changes.
- `uv run pytest -q` green, ruff + `mypy --strict` clean.

**Status:** [~] verified-with-discovery (2026-06-18). convert-resume ran with
ZERO parse warnings. verified.json diff is exactly the 3 intended changes
(skills_familiar populated with 8 items, the 4 project stacks populated, AI
Context Stack url -> github.com/SimBuds/Local-LLM); no other field moved.
Tests: 888 passed, 3 failed - all 3 PROVEN pre-existing (they fail identically
against the `.bak` docx and pre-edit verified.json, so this phase introduced
none of them; it actually fixed the Familiar assertions in
`test_parse_baseline_round_trip`). src/ ruff (62) + mypy (46) errors are
pre-existing - zero source files changed this phase.

**DISCOVERY (re-plan trigger).** The 3 stale regression tests revealed the
Jun 13 docx edit also dropped skills beyond the Familiar row and 2 projects.
Delta vs Resume_Tailoring_Instructions.md Section 2 production-skills list,
absent from the current docx: AWS, Azure, Playwright, Sass, Shopify App
Development, Stripe integration (WORK.md flags Stripe in-progress), Google Tag
Manager. Plus Figma is now in Familiar (Casey's restore decision) but
`test_parse_baseline_positioning_and_atomic_skills` asserts Figma in Core
("promoted after verified Atelier Figma-design work"), and
`test_parse_baseline_round_trip` asserts 6 projects (Casey chose 4), and
`test_audit_peer_broadening...` expects AWS+Azure verified. Resolving the
canonical skill set + Figma placement is a prerequisite for Phases 3-5 and
needs new surface (tests/) not in this plan. Paused for Casey's decisions
before adding the reconciliation phase(s).

Casey's decisions (2026-06-18): restore **AWS, Azure, Playwright** only (leave
Sass, Shopify App Development, Stripe integration, GTM off the docx and trim
them from the instructions' production list). **Figma stays Familiar.** Update
all 3 regression tests to the new canonical baseline. New phases 2b + 2c below.

### Phase 2b - Restore AWS, Azure, and Playwright to the docx and regenerate

**Goal:** Re-add the three high-value dropped skills to the Data & DevOps row
and refresh the profile.

**Files to touch:**
- Baseline_Resume.docx (already backed up by `.bak` from Phase 1) - append
  `Playwright` (next to Jest), `AWS`, `Azure` to the `Data & DevOps:` row.
- kb/profile/* (regenerated by `jobhunt convert-resume`).

**Functions to add/change:** none (python-docx content edit + CLI).

**Verification:** (<= 3 bullets)
- verified.json `skills_data_devops` now contains Playwright, AWS, Azure; no
  other bucket changes vs the Phase 2 output.
- convert-resume runs with zero parse warnings.
- `test_audit_peer_broadening_suppressed_when_sibling_already_matched` is
  expected to go GREEN now that AWS+Azure are verified (it reads verified.json
  from disk). Confirm.

**Status:** [x] done (2026-06-18). Data & DevOps row now ends
`...Jest, Playwright, Python, AWS, Azure`. verified.json diff vs Phase 2: only
Playwright/AWS/Azure added to `skills_data_devops`, no other bucket touched.
convert-resume zero warnings. `test_audit_peer_broadening...` now PASSES, so
only the 2 parse tests remain for Phase 2c.

### Phase 2c - Reconcile the 3 stale regression tests to the new baseline

**Goal:** Make the parse/audit regression guards assert the current canonical
baseline (4 projects, Figma in Familiar, AWS+Azure verified).

**Files to touch:**
- tests/test_parse_docx.py - `test_parse_baseline_round_trip`: project count
  6 -> 4. `test_parse_baseline_positioning_and_atomic_skills`: assert Figma in
  `skills_familiar` and NOT in `skills_core` (was the inverse); keep the atomic
  skills_ai + Dawn-survives + lead-retitle guards.
- tests/test_audit.py - only if Phase 2b did not already green it; adjust the
  AWS/Azure expectation to match verified.json.

**Functions to add/change:** none (test assertions only).

**Verification:** (<= 3 bullets)
- `uv run pytest -q tests/test_parse_docx.py tests/test_audit.py` green.
- Full `uv run pytest -q` returns to 0 failures.

**Status:** [x] done (2026-06-18). `test_audit` was already greened by Phase 2b,
so only the 2 parse tests were edited: `test_parse_baseline_round_trip` project
count 6 -> 4 (both the facts and the round-tripped payload assertions, plus the
stale RR2 comment), and `test_parse_baseline_positioning_and_atomic_skills`
Figma assertion flipped to `in skills_familiar / not in skills_core` (plus its
docstring). Full suite now **891 passed, 0 failed**. ruff clean on the edited
test file. (Pre-existing src/ ruff + mypy errors are unrelated and untouched -
no src/ files changed in this whole plan.)

### Phase 3 - Re-sync WORK.md to the 4-project baseline state

**Goal:** Correct WORK.md's stale claim that the baseline carries all six
projects.

**Files to touch:**
- WORK.md - the "Which projects are on the resume" paragraph (Section 1):
  "carries all six projects" becomes "carries four (jobhunt, Auto-Agent, AI
  Context Stack, SEO-LLM)", and macOS Ventura KVM + the Hybrid coding agent are
  marked long-form-only (off the baseline). No work-history claim changes.

**Functions to add/change:** none.

**Verification:** (<= 3 bullets)
- WORK.md no longer claims six projects are on the baseline.
- The on-baseline stack lines in WORK.md match the docx exactly.

**Status:** [x] done (2026-06-18). The "Which projects are on the resume"
paragraph now states four on-baseline (jobhunt, Auto-Agent, AI Context Stack,
SEO-LLM) with macOS Ventura on KVM + the Hybrid coding agent called out as
long-form-only; both entry headers re-tagged "long-form only, not on the
baseline". WORK.md's per-project stacks already matched the docx (the docx
stacks were sourced from them). No "carries all six" claim remains.

### Phase 4 - Re-sync Resume_Tailoring_Instructions.md to verified.json

**Goal:** Bring the tailoring policy (Section 2 + outcomes) into line with the
verified facts so the prompt stops feeding the model contradictory data.

**Files to touch:**
- Resume_Tailoring_Instructions.md - de-NDA the work-history table (Atelier
  Dacko / SEO AI Marketing Agency / Vintage Gaming Retailer, Sous Chef dates
  to 2015-2025); remove the banned "500+ monthly visitors" and "3 project
  phases" from the claimable-outcomes list; reconcile "~2.5-3 years" to the
  verified summary's "3+ years"; align the Familiar list to the restored docx
  row; update the personal-projects list to the 4 on-baseline plus the
  long-form-only two; trim Sass, Shopify App Development, Stripe integration,
  and Google Tag Manager from the production-skills list (kept off the docx by
  the 2026-06-18 decision) while confirming AWS, Azure, and Playwright stay
  listed; bump the "Last updated" date.

**Functions to add/change:** none.

**Verification:** (<= 3 bullets)
- grep finds no "(NDA)" labels, no "500+ monthly visitors", no "3 project
  phases", and no "2.5" in the file.
- Familiar list matches verified.json `skills_familiar`; the production-skills
  list matches the canonical docx rows (AWS/Azure/Playwright in, the four
  trimmed skills out).

**Status:** [x] done (2026-06-18). Work-history table de-NDA'd (real labels +
verified role titles + Contract/Part-time types + Apr 2023 / Nov 2025
precision); "~2.5-3 years" -> "3+ years"; banned "500+ monthly visitors" and
"3 project phases" removed (the latter reworded to the claimable "over 3+
years"); "9 years" culinary -> "10 years"; production-skills line trimmed
(Sass / Shopify App Development / Stripe / GTM out, AWS/Azure/Playwright kept)
with a note on why trimmed skills aren't tailor-claimable; SEO-LLM added to the
projects list and the on/off-baseline split named; Familiar list already
matched verified.json. Three extra in-surface stale references fixed for
consistency (a "2.5-year history" example, a "2.5-year candidate" heuristic
note, and an "AI Agency" example label). Final greps clean. Note: doc-style
em/en-dash sanitization was NOT done (out of scope; this phase is fact-sync,
and the file already used those dashes throughout).

### Phase 5 - PLAN.md consistency pass and IMPLEMENT.md close-out

**Goal:** Verify PLAN.md's honesty-enforcement section still matches the
restored bucket state and check off this plan.

**Files to touch:**
- PLAN.md - only if the Familiar restore or project changes contradict its
  text (expected: minimal, the buckets are described generically).
- IMPLEMENT.md - check off Phases 1-5.

**Functions to add/change:** none.

**Verification:** (<= 3 bullets)
- PLAN.md skill-bucket and honesty descriptions are consistent with
  verified.json.
- No tracked doc references the old 6-project / Familiar-less / NDA state.

**Status:** [x] done (2026-06-18). PLAN.md required NO change: its skill-bucket
list (lines 154-158) and the Familiar-only-fit cap (line 204) are generic and
fully consistent with the restored buckets (the cap is now functional again
with a non-empty Familiar). AGENTS.md already lists Figma as Familiar, which
confirms Figma-in-Familiar is the original design (the brief "Figma -> Core"
was the abandoned positioning experiment). Repo-wide grep: the only remaining
`Ollama-LLM-Prompts` hits are intentional (WORK.md documents the rename;
IMPLEMENT.md is this plan's own notes) - no stale URL in any resume-facing
artifact. Final full suite: 891 passed, 0 failed. Phases 1-5 complete.

### Phase 6 (deferred, needs Casey's input): Additive factual updates

**Goal:** When Casey supplies the pending facts, route them through WORK.md.
The single tracking home is the Atelier "Pending from Casey" block in WORK.md
(consolidated 2026-06-18): the numbers (Search Console growth, ring-builder
order counts, live SKU count), the go-live statuses (ring builder, e-commerce,
Contentful, Stripe), and the Geeked Out Goods catalog size.

**Routing (per the WORK.md honesty contract):** record the fact in WORK.md
first with a verdict, then edit `Baseline_Resume.docx`, then
`jobhunt convert-resume`, then `uv run python scripts/build_onepager.py` to
refresh the one-pager.

**Status:** [ ] not started (blocked on Casey-supplied facts; tracking home set
up in WORK.md)

## In-flight: work-history factual corrections (2026-06-18)

Scope: Casey supplied real corrections to the work history (not just wording).
Two change the facts, not the phrasing. Route per the WORK.md honesty contract:
WORK.md first (with verdicts), then the docx, then regenerate verified.json,
then sync the instructions + tests. ATS-best-practice wording throughout
(strong verbs, real tech keywords, JD-relevant lead, no invented metrics).

Confirmed facts (2026-06-18):
- **Atelier Dacko:** began as a WordPress portfolio redesigned in Elementor,
  then migrated to Shopify for e-commerce. Custom 16+ page storefront on a
  customized **Dawn 2.0** theme. Migrated products, redirects, images, and
  catalog files to **AWS**. E-commerce is **in progress**, including the
  ring-builder configurator (framed generic + in-progress, VDB stays the
  interview answer). 200+ SKUs dropped pending a live count.
- **Vintage shop:** real name **Geeked Out Goods**. Platform was **Shopify**,
  NOT WordPress/Elementor (current resume is wrong). Title -> **Shopify
  Developer**. Scope: inventory + catalog management, a CSV-sanitizing
  pipeline, product-detail + content management, new product listings, basic
  per-page SEO. 400+ item count dropped pending confirmation.
- **Projects on baseline trim to TWO:** Jobhunt + Auto-Agent (both >= beta).
  AI Context Stack + SEO-LLM are in-progress / not display-ready, so they join
  macOS KVM + the Hybrid agent as long-form-only in WORK.md. JSON-LD stays in
  the Project Stack skills row (defensible via SEO/structured-data work).
- **SEO AI Marketing Agency + Sous Chef:** unchanged.

Contentful in-progress bullet **DROPPED** (2026-06-18, Casey). The Contentful
Certified Professional cert + the `Contentful (Certified Professional)` CMS
skill stay (both real, earned Oct 2025). So Atelier carries 5 bullets.

Open (non-blocking): live SKU/item counts (Casey dropped 200+ SKUs and the
vintage 400+ count); Stripe status.

**Added goal (2026-06-18): the baseline IS the manual-apply master.** It must
pass the full 2026 ATS checklist (Resume_Tailoring_Instructions.md Sections 5
and 6), not just feed the pipeline. Inspection confirmed the FORMAT already
clears the structural levers: real list bullets (17 numPr paragraphs, not typed
`*`/`-`), single column, zero tables/images/icons, Calibri, name 16pt bold,
headings 11pt bold, body 10.5pt, standard section headings, implicit subject.
So hardening is content (Phases 7-10) plus a compliance pass (Phase 11):
strengthen the Summary as a general hook, enforce date-format consistency,
maximize honest keyword coverage, confirm a clean one-page fit.

**Reuse audit:** not applicable. Content edits to Baseline_Resume.docx (via
python-docx), markdown docs, and test assertions, plus the existing
convert-resume CLI. No new code. Mirrors the Phase 1-5 pattern above.

### Phase 7 - Update WORK.md work-history and project on/off-baseline split

**Goal:** Record the corrected work-history facts and the 2-on-baseline
project split in WORK.md with verdicts, before any resume surface changes.

**Files to touch:**
- WORK.md - Atelier Dacko entry (WordPress->Elementor->Shopify arc, Dawn 2.0,
  16+ pages, AWS migration, ring-builder + e-commerce in-progress); Vintage
  entry retitled to Geeked Out Goods / Shopify Developer with the corrected
  Shopify scope; the "which projects are on the resume" paragraph to TWO
  (Jobhunt, Auto-Agent) with AI Context Stack + SEO-LLM re-tagged long-form
  only.

**Verification:** (<= 3 bullets)
- WORK.md Vintage entry says Shopify (no WordPress/Elementor), names Geeked Out
  Goods, and the banned "200+ SKUs"/"400+ items" counts are absent.
- The on-baseline projects paragraph names exactly Jobhunt + Auto-Agent.

**Status:** [x] done (2026-06-18). Atelier "truth/in-progress/measured/banned/
pending" block rewritten (WordPress->Elementor->Shopify arc, Dawn 2.0, 16+
pages, AWS migration, ring-builder + e-commerce in-progress, 200+ SKUs dropped,
Contentful implementation dropped from bullets with cert retained, WordPress
(Elementor) skill re-anchored to Atelier). Vintage entry retitled "Shopify
Developer - Geeked Out Goods" with the corrected Shopify scope and CSV pipeline
(D0 "no pipeline" caution lifted per Casey's own wording), 400+ count dropped.
On-baseline projects paragraph now names exactly jobhunt + Auto-Agent; AI
Context Stack + SEO-LLM re-tagged long-form-only (4 such tags total). JSON-LD
re-anchored to SEO/structured-data work.

### Phase 8 - Rewrite the corrected roles in the docx and regenerate

**Goal:** Bring the Atelier and Geeked Out Goods role entries in the docx to
the corrected, ATS-optimized bullets and refresh the profile.

**Files to touch:**
- Baseline_Resume.docx - replace Atelier's 5 bullets with the corrected set;
  change the Vintage header to `Shopify Developer | Geeked Out Goods` and
  replace its 2 bullets with the corrected Shopify scope.
- kb/profile/* (regenerated by convert-resume).

**Verification:** (<= 3 bullets)
- verified.json Atelier bullets name Dawn 2.0 / 16+ pages / AWS / in-progress
  ring builder; no "200+ SKUs".
- verified.json Vintage role: title "Shopify Developer", employer "Geeked Out
  Goods", bullets name Shopify + the CSV pipeline; no WordPress/Elementor/400+.
- convert-resume zero parse warnings.

**Status:** [x] done (2026-06-18). Atelier's 5 bullets replaced in place
(WordPress->Elementor->Shopify, 16+ page Dawn 2.0, AWS migration, in-progress
ring builder/e-commerce, GSC + on-page SEO). Vintage header rewritten to
`Shopify Developer | Geeked Out Goods`; its 2 bullets replaced + a 3rd inserted
(cloned a bullet paragraph to keep list numbering). convert-resume zero
warnings. verified.json honesty checks: 200+ SKUs / 400+ items / "Vintage
Gaming" / "WordPress Developer" / "Contentful to manage" all absent.
tests/test_parse_docx.py green (18). Project count still 4 here; trim is
Phase 9. **Verification gap (caught in Phase 9): only the parse tests were run
this phase, not the full suite. The full suite revealed 4 breaks that trace to
THIS phase's content changes (numbers 14->16, dropped 200; new employer "Geeked
Out Goods" substring-matching the "Go" keyword). Fixed in Phase 9's expanded
surface below.**

### Phase 9 - Trim the docx PROJECTS to Jobhunt + Auto-Agent and fix the count test

**Goal:** Remove the AI Context Stack and SEO-LLM PROJECTS entries from the
docx and update the project-count regression assertion.

**Files to touch:**
- Baseline_Resume.docx - delete the AI Context Stack and SEO-LLM project
  header + Stack + narrative paragraphs; regenerate.
- tests/test_parse_docx.py - project count 4 -> 2 (facts + payload assertions).
- tests/test_audit.py - (EXPANDED, Casey-approved): update `_good_cover`
  "14+ page" -> "16+ page"; in `test_audit_revise_on_borderline_coverage` and
  `test_audit_block_on_zero_coverage` swap the "Go" must-have for a genuinely
  absent tech (e.g. Scala/Elixir), since the new "Geeked Out Goods" employer
  substring-matches "Go".
- tests/test_cover_validate.py - update `_good_cover` sample to currently
  verified numbers (16+ pages, drop the 200+ SKUs sentence).

**Surface-expansion note:** these two test files were NOT in the original
Phase 9 plan. They broke because Phase 8's number changes (14->16, dropped 200)
and new employer name ("Goods" substring-matches "Go") shifted hard-coded
fixtures. Fixes are mechanical fixture maintenance, surfaced to Casey before
editing. The `phrase_present` substring-match limitation ("Go" matches
"Goods"/"Google") is PRE-EXISTING and shared-infra (risky tier) - logged as a
separate decision, NOT fixed here.

**Verification:** (<= 3 bullets)
- verified.json has exactly 2 projects (Jobhunt, Auto-Agent).
- Full `uv run pytest -q` green (not just the parse tests this time).

**Status:** [x] done (2026-06-18). docx PROJECTS trimmed to Jobhunt +
Auto-Agent (6 paragraphs removed); verified.json = 2 projects;
test_parse_docx counts 4->2. Expanded surface (Casey-approved): test_audit.py +
test_cover_validate.py `_good_cover` samples and CI stubs updated 14->16 and
200/500 dropped; the "Go" must-have swapped for "Scala" in the two coverage
tests (the new "Geeked Out Goods" employer substring-matched "Go"). Full suite
891 passed, 0 failed. The `phrase_present` substring limitation is logged as a
separate risky-tier decision, not fixed here.

### Phase 10 - Sync Resume_Tailoring_Instructions.md and final verify

**Goal:** Bring the instructions' work-history table, quantified outcomes, and
projects list in line with the corrected facts, then prove the suite is green.

**Files to touch:**
- Resume_Tailoring_Instructions.md - Vintage row (Shopify Developer / Geeked
  Out Goods / Shopify); quantified outcomes (Dawn 2.0, 16+ pages, drop 200+
  SKUs + 400+ items, ring builder in-progress, vintage Shopify); projects
  intro to 2 on-baseline.

**Verification:** (<= 3 bullets)
- Instructions name Geeked Out Goods + Shopify Developer; no
  WordPress/Elementor for the vintage role; no 400+/200+ counts.
- Full `uv run pytest -q` green.

**Status:** [x] done (2026-06-18). Work-history table row updated to
`Shopify Developer | Geeked Out Goods` with a Shopify-correction note; quantified
outcomes rewritten (16+ pages + Dawn 2.0, Elementor->Shopify arc, AWS migration,
ring-builder in-progress, Geeked Out Goods Shopify scope; 200+ SKUs and 400+
items removed); projects intro now names the two on-baseline (Job Hunt AI Buddy
+ Auto-Agent) with the four others long-form-only. greps clean, full suite 891
passed.

### Phase 11 - 2026 ATS compliance pass on the manual-apply master

**Goal:** Audit the corrected baseline against the full 2026 ATS checklist and
tune the few content levers a master (non-JD-tailored) resume owns.

**Files to touch:**
- Baseline_Resume.docx - only if the audit finds gaps: tighten the SUMMARY as a
  strong general hook (keep the AI/LLM differentiator + GBC/Dean's List), make
  every date range one consistent format, confirm one-page fit; regenerate if
  any text changed.
- IMPLEMENT.md - record the checklist pass/fail per lever.

**Audit checklist (Resume_Tailoring_Instructions.md Sections 5-6):** single
column; standard fonts/sizes; standard headings; real list bullets; consistent
dates; no tables/graphics/icons/header-footer text; no first-person; no
Objective / "References available"; AI-screen lines (summary + first bullet of
the lead role) carry the strongest signal; authentic voice; AI-tooling surfaced;
contract framing as ownership; one page.

**Verification:** (<= 3 bullets)
- Each checklist lever recorded pass, or fixed then pass.
- One-page fit confirmed (render or page-count check).
- convert-resume still zero warnings; full suite green.

**Status:** [x] done (2026-06-18). Audited the rendered docx (LibreOffice ->
PDF, viewed). 2026 ATS checklist: single column PASS (0 tables/images);
standard fonts PASS (Calibri, name 16pt, headings 11pt, body 10.5pt); standard
headings PASS; real list bullets PASS (numPr); **dates FIXED then PASS**
(aligned to month+year where known: Apr 2023 / Jan 2026 / Jan 2024 /
2015-Nov 2025); no tables/graphics/icons PASS; no header/footer text PASS; no
first-person PASS (verb-first bullets); no Objective / References-line PASS;
AI-screen lines PASS (summary + Atelier lead carry e-commerce/CMS/Contentful/
Shopify/HubSpot/WordPress/TS/React/Node/Ollama/LLM); authentic voice + AI
tooling + contract-as-ownership PASS. **Defect FIXED: removed 1 stray manual
page break that was forcing 3 pages.** One-page lever: **DEFERRED by Casey's
2026-06-18 decision** - the comprehensive baseline stays the 2-page fact
source; the one-page requirement moves to a separate manual-apply doc
(Phase 12) and the pipeline's per-JD tailored outputs. The cram-to-one-page
squeeze (10pt/0.6in) was reverted to readable 10.5pt/0.75in. full suite 891
passed.

### Phase 12 (next, needs Casey's go-ahead): one-page manual-apply resume doc

**Goal:** Produce a separate one-page ATS resume for manual applications,
derived from the verified facts, without trimming the comprehensive baseline.

**Open approach decision (ask before building):**
- (a) Generate via the pipeline's `render_docx` from a master `TailoredResume`
  built off verified.json (reuses the one-page shrink ladder + ATS renderer),
  or
- (b) Hand-author a trimmed one-page docx (Sous Chef to 1 line, fewer bullets,
  drop coursework detail).
Option (a) reuses existing one-page machinery and stays in sync with
verified.json; (b) is more manual but gives finer control. Recommend (a).

**Status:** [x] done (2026-06-18, approach a). Added `scripts/build_onepager.py`
(reuses `_shrink_to_one_page` + `render_docx`) and generated
`Casey_Hsu_Resume_OnePage.docx` from verified.json. Renders to ONE page
(LibreOffice-verified). Composition vs the comprehensive baseline: 4
consolidated skill categories (Project Stack merged into AI & Tooling), Familiar
row dropped (kept on the baseline), Dean's List folded onto the diploma line +
coursework list dropped, bare-domain contact. The estimator (LINES_PER_PAGE=48)
proved ~2-3 lines optimistic for a 6-category master, so the fit was achieved by
the 4-category consolidation rather than the estimate alone. `Baseline_Resume.docx`
+ verified.json untouched (still 4 roles / 2 projects / 8 familiar). ruff clean on
the new script; full suite 891 passed.

**Revision (2026-06-18, Casey):** the first one-pager used the shrink ladder,
which cut roles to 1-2 bullets - too thin. Casey requires **>= 3 work points per
dev role**. `scripts/build_onepager.py` reworked: cap each dev role at 3 bullets
(Atelier/SEO/Geeked), Sous Chef at 1, and DROP the PROJECTS section to fit >= 3
bullets/role on one page (the baseline keeps both projects; the merged AI &
Tooling skill row still carries the project tech). No longer calls
_shrink_to_one_page. Re-rendered: ONE page, 3+3+3+1 bullets. ruff clean.

**Notes for Casey:** (1) regenerate the one-pager after any verified.json change
with `uv run python scripts/build_onepager.py`. (2) For real applications, a
JD-tailored one-pager via `jobhunt apply --url <URL>` beats this generic master.
(3) `Baseline_Resume.docx` and `Casey_Hsu_Resume_OnePage.docx` are NOT gitignored
- decide whether personal resume docs should be ignored.

### Phase 13 - Apply CMS-focused baseline audit decisions

**Goal:** Record the confirmed CMS-focused baseline state across the resume
source artifacts.

**Files to touch:**
- WORK.md - mark the 400+ Geeked Out Goods inventory size as confirmed, remove
  Search Console growth and live SKU count from the pending numeric facts, keep
  Atelier e-commerce and ring builder as in progress, and record AI tooling as
  a side-project interest for the baseline.
- Resume_Tailoring_Instructions.md - sync the verified facts, quantified
  outcomes, and 2026 positioning guidance to the new CMS-first baseline.
- kb/policies/tailoring-rules.md - mirror the 2026 positioning rule change for
  prompt injection.
- Baseline_Resume.docx - update the summary toward CMS Developer, fix role/date
  separators for parser clarity, add the 400+ inventory fact, and remove the
  second Sous Chef bullet.
- kb/profile/* - regenerate with `jobhunt convert-resume`.
- Casey_Hsu_Resume_OnePage.docx - regenerate from verified.json.
- tests/test_parse_docx.py - update parser guards only if the visible role
  title changes.

**Functions to add/change:** none.

**Note on phase size:** this exceeds the normal file count because the project
honesty contract requires the source docs, docx, verified profile, and one-page
derivative to move together. Splitting would knowingly leave one artifact stale.

**Reuse audit:** not applicable. Content edits via existing `python-docx`
handling, then existing `jobhunt convert-resume` and
`scripts/build_onepager.py`. No new code or helpers.

**Verification:** (<= 3 bullets)
- `jobhunt convert-resume` completes with zero parse warnings and verified.json
  reflects CMS-first summary, 400+ inventory, no Search Console bullet, one
  Sous Chef bullet, and clean role dates.
- `uv run pytest -q tests/test_parse_docx.py tests/test_audit.py
  tests/test_cover_validate.py` passes.
- `uv run python scripts/build_onepager.py` regenerates the one-pager.

**Verification results (2026-06-18):**
- `.venv/bin/jobhunt convert-resume` completed with 4 roles, 2 projects, 29
  core skills, 6 project skills, and 8 familiar skills.
- `uv run pytest -q tests/test_parse_docx.py tests/test_audit.py
  tests/test_cover_validate.py` passed with 84 tests in 0.58s. The command
  required escalation because the `uv` cache is outside the workspace.
- `uv run python scripts/build_onepager.py` wrote
  `Casey_Hsu_Resume_OnePage.docx`. The current baseline and one-page docx
  artifacts contain no Search Console text, no live SKU text, no tab-separated
  role date text, and do contain the 400+ inventory fact.

**Documentation check (2026-06-18):**
- `IMPLEMENT.md` updated with this phase result.
- `PLAN.md` unchanged because architecture, data structures, and feature scope
  did not change.
- `README.md` unchanged because commands, setup, and user-facing APIs did not
  change.

**Status:** [x] done
