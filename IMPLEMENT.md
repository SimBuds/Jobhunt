# IMPLEMENT.md: Execution engine

Pillar 4 of the documentation architecture defined in [AGENTS.md](AGENTS.md).
This file is the granular, phase-by-phase breakdown of in-flight work. Agents
**read** this at the start of every phase (Phase 1 and Phase 3 of the Workflow
Contract) and **write** to it as part of the Definition of Done, checking off
completed phases and logging deferred work as new phases. Never leave a plan in
conversational context alone. It lives here.

## Phase template

Copy this block for each phase of an approved plan. One phase = one atomic,
revertable commit. See Phase-Sizing Rules in `AGENTS.md`.

```
### Phase <N> - <one-sentence goal, no "and">

**Goal:** <single declarative sentence>

**Files to touch:**
- path/to/file - what changes

**Functions to add/change:**
- module.function - add | change - what it does

**Reuse audit:** (per Reuse-First Rule in AGENTS.md)
- Search terms: `<grep/rg terms used>`
- Candidates found: <list, or "none">
- Why not reused: <reason per candidate>

**Verification:** (<= 3 bullets)
- <how to prove it works, test name, or manual E2E output>

**Status:** [ ] not started | [ ] in progress | [ ] done
```

## Planned - Workflow audit remediation (2026-07-24)

Origin: a usage audit of the live DB and `data/applications/` artifacts, not a
feature request. The finding that drives the ordering: **ingest and generation
work; consumption does not.** 928 jobs ingested, 487 scored, 95 currently
scoring >= 55 unapplied and undeclined; 12 pipeline applications, all inside a
4-day burst (2026-06-24 to 06-27), 1 since. Zero pipeline responses. Both
LinkedIn applications drew a response, one an interview.

Compounding the above: 11 of 27 dirs in `data/applications/` have **no row in
`applications`**, three of them holding complete 7-file artifact sets with
`ship` verdicts (`adzuna_ca_5778302570` 06-27, `job_bank_ca_49852899` 07-09,
`adzuna_ca_5800664772` 07-16). Finished work is being generated and then lost
from tracking, so `list --no-reply`, `list --week`, and `analyze funnel` all
under-report. That is Phase A1 and it blocks the value of every later phase.

Sequencing rationale: A1 stops the bleeding (walking-skeleton bias - the
thinnest end-to-end fix to the data-integrity path). A2 reconciles the existing
mess. A3/A4 remove the daily copy-paste friction. A5 makes the funnel
measurable. A6 makes the backlog visible. A7 stops growing what nobody drains.
A8 is the experiment the whole audit points at.

### Phase A1 - Persist the application row before the browser step

**Goal:** Write the `applications` row at artifact-render time so no completed
tailoring run can be lost to a browser crash, a Ctrl-C, or an abandoned prompt.

**Files to touch:**
- src/jobhunt/commands/apply_cmd.py - reorder `_apply_io_phase`
- tests/test_apply_cmd.py - regression for the early-write ordering

**Functions to add/change:**
- `apply_cmd._apply_io_phase` - change - call `_record_application` with status
  `drafted` immediately after `_render_artifacts` (currently line 890), then
  call it again with the confirmed status after `_confirm_submission_status`.
  Today the single call sits at line 903, *after* `_run_browser_step` (896) and
  the submit prompt (900) - both of which can exit the process.
- `apply_cmd._record_application` - change - accept `plan_path=None` so the
  pre-browser write is legal before a fill-plan exists.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `grep -rn "_record_application\|INSERT INTO applications\|INTO applications" src/`
- Candidates found: `db.py:209` (the upsert SQL), `apply_cmd.py:1087`
  (`_record_application`), `apply_cmd.py:903` (its only call site).
- Why not reused: both *are* reused as-is. `db.py:209` is already an upsert, so
  calling it twice for one job is idempotent by construction and needs no new
  write path. No new function is introduced in this phase.

**Verification:** (<= 3 bullets)
- New test: simulate `_run_browser_step` raising; assert a `drafted` row exists
  for the job afterward. Fails on current ordering, passes after.
- New test: normal path still lands the confirmed status (`applied`), not
  `drafted` - proves the second write wins.
- `uv run pytest tests/test_apply_cmd.py` green.

**Result (2026-07-24):**
- `_apply_io_phase` now writes `drafted` immediately after `_render_artifacts`;
  the post-prompt write is unchanged and overwrites via the existing
  `ON CONFLICT(job_id)` upsert in `db.py:209`.
- Both tests land in `tests/test_apply_pipeline_e2e.py`, **not** the planned
  `tests/test_apply_cmd.py` - that file does not exist. The e2e file is the
  established home for `_apply_one` flow tests. Same intent, different path;
  logged rather than silently substituted.
- The planned `_record_application` signature change was **not needed**: the
  parameter is already `plan_path: Path | None`, so the pre-browser write with
  `None` was already legal. No signature changed in this phase.
- Verified safe against re-selection: `_unapplied_top_query` (line 456) already
  admits `a.status = 'drafted'`, so an early row cannot drop a job out of a
  later `--top` / `--best` run.
- `test_apply_one_records_drafted_before_browser_crash` fails with the src
  change stashed and passes with it (confirmed by `git stash` round-trip).
- Gates: `uv run pytest` 970 passed (968 + 2 new); `ruff check` clean;
  `mypy src` clean, 76 files.
- README not touched: no user-facing API, flag, or instruction changed. The
  existing "answered `no` or cancelled -> `drafted` row" sentence stays
  accurate and is now also true for crashes.

**Status:** [x] done

### Phase A2 - Reconcile orphan artifact directories with `jobhunt db gc`

**Amended 2026-07-24, before implementation.** The original goal was "report and
optionally remove" orphan dirs. Deleting is wrong for three of the eleven
(`adzuna_ca_5778302570`, `job_bank_ca_49852899`, `adzuna_ca_5800664772`): they
hold complete `ship`-verdict resume + cover sets, i.e. real finished work that
A1 would have kept. A second discovery narrows the scope further -
`job_bank_ca_49859353` has verdict `block`, and a `block` verdict writing
audit.json with no docs and no application row is **correct** behavior per the
audit rules, not an orphan at all. `gc` must not touch it.

**Goal:** Reconcile `data/applications/` directories against the `applications`
table, classifying each orphan by what it actually holds.

**Files to touch:**
- src/jobhunt/commands/db_cmd.py - new `gc` subcommand
- tests/test_db_gc.py - classification, adopt, and prune coverage
- README.md - hidden-maintenance command list

**Functions to add/change:**
- `db_cmd._classify_orphans` - add - map each dir to one of: `blocked`
  (audit.json verdict == block; expected, never actioned), `adoptable` (holds
  rendered .docx), `stale` (no rendered docs).
- `db_cmd.gc` - add - dry-run report by default; `--adopt` writes `drafted`
  rows for adoptable dirs; `--prune` deletes stale dirs.

**Reuse audit:**
- Search terms: `grep -rn "def reset\|shutil.rmtree\|rmtree" src/jobhunt/commands/db_cmd.py`,
  `grep -rn "_safe_id\|def upsert_application" src/`
- Candidates found: `db_cmd.reset` (line 68) + its `shutil.rmtree` loop (line
  100); `apply_cmd._safe_id` (line 1181); `db.upsert_application` (line 190).
- Why not reused: `reset` is unconditional and total (DB, artifacts, cache,
  browser profile, `kb/profile/`); `gc` is selective and never touches the DB
  file, so only the rmtree + confirm *pattern* carries over. `_safe_id` and
  `upsert_application` **are** reused directly - `_safe_id` is the exact
  function `apply_cmd` used to name these dirs, so it is the only correct way
  to invert dir-name -> job_id, and `upsert_application` is the same writer A1
  uses, keeping one application-write path.

**Verification:**
- New test: a `block`-verdict dir is classified `blocked` and survives both
  `--adopt` and `--prune`.
- New test: `--adopt` creates exactly one `drafted` row pointing at the .docx;
  `--prune` removes only the docless dirs.
- Live: `uv run jobhunt db gc` reports 3 adoptable, 7 stale, 1 blocked.

**Result (2026-07-24):**
- `db gc` added with three modes: bare (report), `--adopt`, `--prune`
  (`--force` skips the prune confirmation, mirroring `reset`).
- Live dry run matched the prediction exactly: 3 adoptable, 7 stale, 1 blocked.
  The 7 stale split 5 empty shells + 2 audit-only `ship` dirs that died between
  audit and render.
- `applied_week` on an adopted row comes from the directory mtime, not today -
  dating a backfill as current would corrupt the weekly rollup.
- `_adopt` returns False when the `jobs` row is gone (FK target missing) and
  `gc` reports those as skipped rather than failing the run.
- Gates: `uv run pytest` 976 passed (970 + 6 new); `ruff check` clean;
  `mypy src` clean, 76 files.
- README updated (hidden-maintenance list + a paragraph on the taxonomy).
  PLAN.md not touched - no architecture or scope change.
- **Not yet executed on real data.** `--adopt` and `--prune` are destructive-
  tier per the Blast-Radius rules; awaiting explicit approval.

**Status:** [x] done (command shipped; live mutation pending approval)

### Phase A3 - Extract the job-reference resolver into a shared helper

**Goal:** Move `track_cmd._resolve_ref` into a shared module with a scope
parameter, leaving `track` behavior byte-identical.

**Files to touch:**
- src/jobhunt/commands/_refs.py - new module
- src/jobhunt/commands/track_cmd.py - delegate to it
- tests/test_refs.py - resolver unit tests

**Functions to add/change:**
- `commands._refs.resolve_job_ref(conn, ref, *, scope)` - add - `scope="applied"`
  reproduces today's join against `applications`; `scope="jobs"` matches any row
  in `jobs`, which `apply` needs since its targets have no application row yet.
- `track_cmd._resolve_ref` - change - thin delegation, signature unchanged.

**Reuse audit:**
- Search terms: `grep -rn "def _resolve_ref\|def _resolve_by_id\|def _resolve_job_id\|LIKE ?\|lower(company)" src/`
- Candidates found: `track_cmd._resolve_ref` (line 55), `apply_cmd._resolve_by_id`
  (line 441), `interview_prep_cmd._resolve_job_id` (line 221),
  `pipeline/_answer_index.py:127` (a `LIKE` over question text).
- Why not reused: `track_cmd._resolve_ref` is the model and becomes the shared
  implementation - it already handles exact-id-wins, case-insensitive
  company+title substring, and ambiguity-lists-candidates. It cannot be reused
  *unchanged* because its SQL hard-joins `applications` (line 71), which
  excludes every unapplied job `apply` targets. `apply_cmd._resolve_by_id` is
  exact-match only, no fragment support. `interview_prep_cmd._resolve_job_id`
  is an intake router (id vs URL vs stdin), a different job. `_answer_index`
  searches question text, unrelated domain.

**Verification:**
- New test: `scope="jobs"` resolves a fragment for a job with no application row.
- New test: ambiguous fragment still exits 1 listing candidates.
- `uv run pytest tests/test_track_cmd.py` green - no behavior change to `track`.

**Result (2026-07-24):**
- `commands/_refs.py` added; `track_cmd._resolve_ref` is now a one-line delegate
  at `scope="applied"`. The pre-existing `tests/test_track_cmd.py` tests call
  `_resolve_ref` directly and assert on its error strings, so they act as a
  free regression guard on the extraction - all 32 track/refs tests green.
- Two additions beyond a literal lift, both deliberate:
  - The `jobs` scope excludes declined postings
    (`decline_reason IS NULL OR = ''`). A fragment matching only declined rows
    reads as "no match" rather than resolving to something the pipeline
    already rejected.
  - Ambiguous matches cap the candidate list at `_MAX_CANDIDATES = 10` with a
    `… +N more` tail. A bare company fragment in the jobs scope can hit dozens
    of postings; the `applied` scope never had that problem.
- Live read-only check against `data/jobhunt.db`: `faire` (jobs) correctly
  errors as ambiguous listing 8 live postings, `instacart` (applied) resolves
  to `greenhouse:instacart:7963661`, `shopify` (jobs) resolves to
  `manual:66601bea62cc`.
- Gates: `uv run pytest` 985 passed (976 + 9 new); `ruff check` clean;
  `mypy src` clean, 77 files.
- No doc pillar updated: `track` behaves identically and no user-facing surface
  changed. The user-visible capability lands in A4, which documents it.

**Status:** [x] done

### Phase A4 - Accept company fragments in `apply` and `interview-prep`

**Goal:** Let both commands take a company/title fragment wherever they take a
job id, so daily use stops requiring `greenhouse:faire:8603123002`.

**Files to touch:**
- src/jobhunt/commands/apply_cmd.py - resolve the positional arg
- src/jobhunt/commands/interview_prep_cmd.py - resolve the positional arg
- tests/test_apply_cmd.py, tests/test_interview_prep_cmd.py

**Functions to add/change:**
- `apply_cmd._resolve_by_id` - change - delegate to `resolve_job_ref(scope="jobs")`.
- `interview_prep_cmd._resolve_job_id` - change - route a non-matching positional
  through `resolve_job_ref(scope="jobs")` before the existing intake branches.

**Reuse audit:**
- Search terms: as Phase A3 (same surface).
- Candidates found: `commands._refs.resolve_job_ref` from A3.
- Why not reused: it is reused - that is the entire phase. No new helper.

**Verification:**
- New test: `jobhunt apply faire` resolves to the Faire job id.
- New test: `jobhunt interview-prep opentable` resolves without a job id.
- Existing exact-id tests in both files still pass.

**Result (2026-07-24):**
- `apply_cmd._resolve_by_id` delegates to `resolve_job_ref(scope="jobs")`.
- `interview_prep_cmd._resolve_positional` added and wired into the positional
  branch of `_resolve_job_id`. It falls back to the raw ref on `sqlite3.Error`
  (DB file absent or schema not migrated) so `_load_job` keeps ownership of the
  authoritative "no such job" error - this is what preserves the pre-existing
  `test_resolve_job_id_passthrough_existing_id` case, which runs without a DB.
- Tests landed in `tests/test_apply_selection.py` (4) and
  `tests/test_manual_intake.py` (2 new + 1 docstring clarified), **not** the
  planned `tests/test_apply_cmd.py` / `tests/test_interview_prep_cmd.py`:
  neither exists, and these are the files that already own the two resolvers.
- Deliberate coverage of the declined-job edge: the fragment path skips
  declined postings, so a test pins that an *exact* id still resolves one.
- Live: `jobhunt apply faire` errors as ambiguous listing 8 postings and exits
  before any LLM work; `jobhunt apply zzznotathing` prints "no job matches";
  `_resolve_by_id(conn, "shopify")` resolves to `manual:66601bea62cc`
  (Saje Natural Wellness).
- Gates: `uv run pytest` 991 passed (985 + 6 new); `ruff check` clean;
  `mypy src` clean, 77 files.
- Docs: README notes fragments work anywhere a job id does; AGENTS.md gains a
  "Job references" block making the shared resolver the rule for new commands.
  PLAN.md not touched - no architecture change.

**Status:** [x] done

### Phase A5 - Add `jobhunt track sweep` for stale no-response applications

**Goal:** Surface applications past a no-response threshold and bulk-mark them
`ghosted` so the funnel stops treating silence as pending.

**Files to touch:**
- src/jobhunt/commands/track_cmd.py - new `sweep` subcommand
- tests/test_track_cmd.py - threshold and bulk-mark coverage
- README.md + AGENTS.md - command surface

**Functions to add/change:**
- `track_cmd.sweep` - add - list applied rows with no `response_received_at`
  older than `--older-than` (default `21d`); `--apply` marks each `ghosted`
  through the existing lifecycle path.

**Reuse audit:**
- Search terms: `grep -rn "ghosted\|no_reply\|older_than\|_parse_duration" src/jobhunt/commands/*.py`
- Candidates found: `list_cmd._parse_older_than` (line 145), `list_cmd._query`
  `no_reply` branch (line 257), `track_cmd.outcome` (line 329),
  `apply_cmd._run_lifecycle` (line 299).
- Why not reused: `_parse_older_than` and `_run_lifecycle` *are* reused -
  `_parse_older_than` imported from `list_cmd`, and the mark path goes through
  `_run_lifecycle` exactly as `track outcome` already does, keeping one
  lifecycle write path per the AGENTS.md note. `list_cmd._query` is not reused:
  it renders job rows for human scanning, while `sweep` needs the id set for a
  write loop.

**Verification:**
- New test: an application applied 30d ago with no response appears in bare
  `sweep`; one applied 5d ago does not.
- New test: `sweep --apply` sets `outcome='ghosted'` on exactly the stale rows.
- Live: `uv run jobhunt track sweep` names the 12 June pipeline applications.

**Result (2026-07-24):**
- `track sweep` added with `--older-than` (default `21d`) and `--apply`.
  `_parse_older_than` imported from `list_cmd` and every write routed through
  `apply_cmd._run_lifecycle`, so the one-lifecycle-write-path rule holds.
- Selection predicate is deliberately narrow and pinned by a test that seeds
  all five cases: stale-silent (selected), recent, already-responded,
  already-outcomed, and `interviewing` (all skipped).
- Live dry run named **11** applications, not the 12 predicted in the plan.
  The 12th (`manual:66601bea62cc`) was applied 2026-07-23, one day before the
  threshold - the prediction was wrong, the command is right.
- Gates: `uv run pytest` 995 passed (991 + 4 new); `ruff check` clean;
  `mypy src` clean, 77 files.
- Docs: README weekly block + a note that `sweep` is the only writer of
  non-responses; AGENTS.md command surface documents the narrow predicate.
  PLAN.md not touched.
- **Not yet executed on real data.** `sweep --apply` is a bulk lifecycle
  write; awaiting explicit approval alongside the pending `gc` mutations.

**Status:** [x] done (command shipped; live mutation pending approval)

### Phase A6 - Print an action-board header on bare `jobhunt list`

**Goal:** Make one default command answer "what do I do today" without
remembering three flag combinations.

**Files to touch:**
- src/jobhunt/commands/list_cmd.py - header renderer
- tests/test_list_cmd.py - header content

**Functions to add/change:**
- `list_cmd._render_action_board` - add - one line above the default listing:
  N ready to apply (>= min_score, unapplied) | N awaiting reply > 14d | N
  needing a lifecycle update.

**Reuse audit:**
- Search terms: `grep -n "def \|typer.echo" src/jobhunt/commands/list_cmd.py`
- Candidates found: `_render_weekly_footer` (line 296), `_query` (line 210),
  `_parse_older_than` (line 145).
- Why not reused: `_render_weekly_footer` is the closest analogue and its
  counter-line *format* is followed for consistency, but it is scoped to a
  single ISO week and reads `applied_week`, whereas the board is week-agnostic
  and spans three different predicates. The three counts reuse `_query`'s
  existing WHERE fragments rather than introducing new SQL.

**Verification:**
- New test: seeded DB produces the expected three counts in the header.
- New test: header is suppressed when any explicit filter flag is passed.
- `uv run jobhunt list` shows a non-zero ready count against the live 95.

**Result (2026-07-24):**
- `_action_board_counts` + `_render_action_board` added; gated on the
  pre-existing `default_apply_targets` flag, so any explicit filter suppresses
  the board with no new conditional logic.
- Third queue refined from the plan's vague "needing a lifecycle update" to
  **drafted, not submitted** - a concrete queue that A1/A2 now populate
  correctly, and one the user can act on directly.
- Non-empty queues print their command (`list --drafted`, `track sweep`) so the
  board is navigable rather than merely informative.
- **Correction to the audit's headline number.** The board read 113 ready, not
  the 95 quoted in the 2026-07-24 audit and in this file's preamble. Cause: the
  audit query used the README's documented default floor of 55, but the live
  `[pipeline] min_score` is **50**. At 50 the backlog is 113. The board is
  right - it reads the configured floor - and the audit figure was measured at
  the wrong one. A7's ceiling must be chosen against 113, not 95.
- Gates: `uv run pytest` 998 passed (995 + 3 new); `ruff check` clean;
  `mypy src` clean, 77 files.
- Docs: README daily block + a bullet describing the board and its suppression
  rule. PLAN.md not touched.

**Status:** [x] done

### Phase A7 - Gate auto-discovery on unapplied-backlog depth

**Goal:** Skip the post-scan discovery probe while the actionable backlog
already exceeds a configured ceiling.

**Files to touch:**
- src/jobhunt/config.py - new setting
- src/jobhunt/commands/scan_cmd.py - gate the call
- tests/test_scan_cmd.py - gate on/off
- README.md - config table

**Functions to add/change:**
- `config.IngestConfig.discover_backlog_ceiling` - add - int, default 40; 0
  disables the gate and restores today's behavior.
- `scan_cmd._scan` - change - at line 157, extend the existing
  `cfg.ingest.auto_discover and not no_discover and inserted` condition with a
  backlog count check, printing the reason when it skips.

**Reuse audit:**
- Search terms: `grep -rn "auto_discover\|no_discover" src/jobhunt/`
- Candidates found: `config.py:83` (`auto_discover`), `scan_cmd.py:157` (the
  gate), `scan_cmd._auto_discover` (line 227).
- Why not reused: the existing boolean is reused and the new ceiling composes
  with it rather than replacing it - `auto_discover=false` must keep meaning
  "never probe". The backlog count reuses the same predicate as A6's "ready to
  apply" counter; if A6 lands first this is a direct call, otherwise the SQL is
  inlined and A6 refactors both to one helper.

**Verification:**
- New test: backlog above ceiling skips `_auto_discover`; below it runs.
- New test: `discover_backlog_ceiling=0` preserves current behavior.
- Live: `uv run jobhunt scan --limit 5` prints the skip reason at backlog 95.

**Result (2026-07-24):**
- `IngestConfig.discover_backlog_ceiling` added (default 40) and the gate wired
  into the existing `auto_discover and not no_discover and inserted` condition
  in `_scan`. The skip prints the live backlog, the ceiling, and how to change
  it, so it never looks like a silent failure.
- `_ready_backlog` calls A6's `_action_board_counts(...)["ready"]` rather than
  re-implementing the predicate: the number the gate throttles on is the same
  number `jobhunt list` shows the user.
- Tests in a new `tests/test_scan_discover_gate.py` (6): below/at ceiling,
  `0` disables, applied+declined excluded, `min_score` respected, default is 40.
- Gates: `uv run pytest tests/test_scan_discover_gate.py` 6 passed;
  `ruff check` clean; `mypy src` clean, 77 files.
- **Full-suite caveat:** the whole suite reports 11 failures at this commit
  (`test_audit`, `test_cover_validate`, `test_parse_docx`, `test_query_planner`).
  None are caused by A7 - the suite was 998-green at the end of A6, and these
  tests assert against baseline-resume *content*. `Baseline_Resume.docx` was
  replaced at 22:00 and `kb/profile/verified.json` regenerated at 22:02, mid-
  phase. See Phase A9 - the parser silently dropped most of the new resume.
- Live `scan` verification deferred: running it now would score against the
  corrupted `verified.json`. Deferred to after A9.
- Docs: README config block documents the ceiling. PLAN.md not touched.

**Status:** [x] done (live scan check deferred to post-A9)

### Phase A8 - Run the two-channel volume experiment

**Goal:** Measure whether lane-resume volume through manual channels converts
better than full-pipeline tailoring, using the funnel the earlier phases fixed.

This phase is a **measurement protocol, not a code change.** It cannot be
completed by an agent: it requires Casey to submit real applications over
calendar time. What the agent can do is set the baseline, regenerate the lane
resumes, and define the stop condition in advance so the result is not
retrofitted to whatever happens.

**Hypothesis:** the pipeline optimizes quality-per-application while the
binding constraint is applications-per-week. If true, a cheaper high-volume
channel wins on interviews-per-hour even at lower per-application quality.

**Protocol:**
- Arm A (volume): lane base resumes from `data/resumes/`, manual channels
  (LinkedIn/Indeed/referral), logged with `jobhunt track applied --channel`.
  No LLM per application.
- Arm B (quality): full `jobhunt apply` pipeline, reserved for scores >= 70.
- Run until each arm has >= 10 applications, or 3 weeks elapse - whichever
  comes first. Do not read results early; n=2 is what makes the current
  LinkedIn signal directional rather than conclusive.
- Read out with `jobhunt analyze funnel --by channel` and
  `jobhunt analyze response-rate --by channel`.
- Decision rule, fixed now: if Arm A's response rate is >= 2x Arm B's, demote
  the tailor pipeline to scores >= 80 only. If Arm B leads on response rate,
  keep the pipeline and instead attack its throughput. If neither separates,
  the constraint is upstream of both - targeting, not tooling.

**Baseline, measured 2026-07-24** (`jobhunt analyze funnel --by channel`):

```
Bucket          Applied   Resp         Intvw        Offer
pipeline             12      0 (  0%)      0 (  0%)     0 (  0%)  median-response -
linkedin              2      2 (100%)      1 ( 50%)     0 (  0%)  median-response 2d
TOTAL                14      2 ( 14%)      1 (  7%)     0 (  0%)  median-response 2d
```

Caveat carried into the readout: the 12 pipeline applications are 4 weeks old
with no lifecycle updates recorded, so their 0% is partly a tracking artifact.
Phase A5's `sweep` must run before this baseline is treated as real.

**Verification:**
- Lane resumes regenerated and dated after the A-phase work.
- Baseline recorded above, before any experimental applications.
- Readout uses the two `analyze` commands named, unchanged.

**Baseline re-measured 2026-07-26, after the prerequisite writes.** The
2026-07-24 reading was not trustworthy: the 12 pipeline applications sat in
`applied` with `outcome IS NULL`, so a 0% response rate was indistinguishable
from "still waiting". Two writes fixed that:

- `jobhunt db gc --adopt` recovered the three `ship`-verdict artifact sets that
  A1 would have kept (`adzuna_ca:5778302570`, `adzuna_ca:5800664772`,
  `job_bank_ca:49852899`), each dated to its own ISO week (W26, W28, W29) via
  directory mtime rather than today. `gc` now reports 0 adoptable.
- `jobhunt track sweep --apply` recorded **11 non-responses** as `ghosted`.

Outcome column, which was 100% NULL before:

```
ghosted    pipeline  11
(pending)  pipeline   8      (5 drafted, never submitted; 3 recent)
(pending)  linkedin   2
```

**Baseline (2026-07-26):**

```
Bucket          Applied   Resp         Intvw        Offer
pipeline             12      0 (  0%)      0 (  0%)     0 (  0%)  median-response —
linkedin              2      2 (100%)      1 ( 50%)     0 (  0%)  median-response 2d
TOTAL                14      2 ( 14%)      1 (  7%)     0 (  0%)  median-response 2d
```

The numbers are unchanged from 07-24; their **meaning** is not. Pipeline 0% is
now a measured non-response across 11 explicitly-ghosted applications, not an
artifact of never recording silence. That is what makes it a legitimate control
arm. n is still tiny - 2 LinkedIn applications cannot carry a 100% rate - which
is exactly why the decision rule was fixed in advance.

**Still required before readout:** >= 10 applications per arm, or 3 weeks.
Neither arm has that yet.

**Arm A kickoff (2026-07-26).** `jobhunt resume --focus all` regenerated the
lane base resumes against the repaired `verified.json`. Two of three shipped:
`Casey_Hsu_Resume_AI_Automation.docx` and `Casey_Hsu_Resume_CMS_Ecommerce.docx`.

- **Technical SEO lane failed the fabrication guard** after exhausting its
  retries: `skill not in verified facts: 'Core Web Vitals (LCP, CLS, INP)'`.
  The guard is correct - "LCP, CLS, INP" appear nowhere in the profile.
  Root cause is *granularity*, not the model: the resume bundles all of SEO
  into two atomic items, `technical SEO (Core Web Vitals, PageSpeed, JSON-LD)`
  and `Google Search Console / Analytics / Tag Manager`. A lane whose entire
  premise is SEO has nothing decomposable to surface, so the model reaches for
  a more specific phrasing and is rightly rejected. The stale 2026-07-07 file
  remains in `data/resumes/`.
  Two ways out, both the author's call: split those parentheticals into
  separately claimable items in the resume, or retire the lane -
  `kb/profile/verified-notes.md` records that "seo specialist" scans returned
  5/5 declines, so an SEO-titled lane may not be worth maintaining.

**Environment note.** The first run died with
`CUDA error: an illegal memory access was encountered`, after which Ollama
reported `qwen3.5:9b` resident at 5.7 GB while `nvidia-smi` showed 1.3 GB used
on the 10 GB card - a wedged post-fault state. Unloading via
`POST /api/generate {"keep_alive":0}` cleared it and the retry ran clean. Worth
knowing before blaming the pipeline for a hung `scan`.

**Status:** [~] baseline valid and recorded; awaiting real application volume

### Phase A9 - Restore full parse coverage for the reformatted baseline resume

**BLOCKING. Raised 2026-07-24 22:0x, mid-A7, when `Baseline_Resume.docx` was
replaced and `convert-resume` re-run.** The parser silently dropped most of the
new resume. Current `kb/profile/verified.json` states, as verified truth:

```
skills_core       0 items   (13 dropped: JS/TS, Python, React, Next.js, Node, ...)
skills_ai         0 items   (dropped: Claude API, agentic architecture, Ollama, ...)
work_history      1 item    — "Sous Chef & Team Lead, JOEY Restaurant Group"
projects          0 items
```

Every developer role (Atelier Dacko, Neurative AI, Geeked Out Goods) was
dropped. Until this is fixed, `scan` scores against a sous-chef profile, and
the fabrication guard rejects Casey's real skills as unverified because they
are genuinely absent from the snapshot. Do not run `scan`, `apply`, or
`resume` before this lands.

**Two independent root causes, both format-coupling in the parser:**

1. `_SKILL_LABEL_ALIASES` (parse_docx.py:118) has no key for the new labels
   `"languages & frameworks"` or `"ai & automation"`, so both buckets are
   dropped whole. The alias table is an allow-list of ~20 hand-listed strings.
2. `_ROLE_LINE_RE` (parse_docx.py:159) requires `Title | Employer<sep>Dates`.
   The new resume uses `Employer — Descriptor   Dates` (em-dash, no pipe), so
   no role header matches, every bullet hits the "bullet before any role
   header" branch, and the whole section is discarded.

**Goal:** Parse the reformatted baseline with zero warnings.

**Files to touch:**
- src/jobhunt/resume/parse_docx.py - alias table + role-header pattern
- tests/test_parse_docx.py - fixtures for both formats
- kb/profile/* - regenerate via convert-resume

**Decision (settled 2026-07-24):** widen the parser, and cover **both** formats
with `.docx` fixtures so neither the resume nor the parser is a single point of
truth. Rejected: reformatting the resume back to the documented shape (fastest,
but the next reformat breaks it again and it helps no other user) and
warn-only (fixes the silent-failure mode without fixing this resume - folded in
anyway as a sub-task, since a broken profile should never be written silently).

**Re-planned 2026-07-24 after a finding that shrinks the code scope.** The
title-less role headers are not just a parser gap - they are an information
*regression* in the document. `Resume_Tailoring_Instructions.md` lines 37-39
already define canonical titles for all three engagements (CMS / E-commerce
Developer (Contract) @ Atelier Dacko; CMS Developer (Contract, Part-time) @
Neurative AI; Shopify Developer (Contract) @ Geeked Out Goods). The reformat
dropped titles the project already treats as verified truth.

Restoring them in the **existing** pipe form parses with the current
`_ROLE_LINE_RE` untouched, keeping the em-dash descriptor inside `employer`:

```
"CMS / E-commerce Developer (Contract) | Atelier Dacko — Custom Jewelry Brand   Apr 2023 – Present"
  -> title='CMS / E-commerce Developer (Contract)'
     employer='Atelier Dacko — Custom Jewelry Brand'
     dates='Apr 2023 – Present'
```

So the role half of this phase is a **document** fix, not a code fix, and the
`Role`-shape question is moot - titles exist again.

**Sub-tasks (revised):**
1. `_SKILL_LABEL_ALIASES` += `"languages & frameworks"` -> Core,
   `"ai & automation"` -> AI & Tooling. Needed regardless of document format.
2. Restore role titles in `Baseline_Resume.docx` (pipe form). No parser change.
3. Regenerate `kb/profile/` and confirm `warnings == []`.
4. **Deferred to A9c:** dash-form `_ROLE_DASH_RE` + dual-format fixtures. Still
   worth having so another user's title-less resume degrades loudly instead of
   silently, but it is no longer on the blocking path.

**Reuse audit:**
- Search terms: `grep -n "_SKILL_LABEL_ALIASES\|_ROLE_LINE_RE\|role header" src/jobhunt/resume/parse_docx.py`
- Candidates found: `_SKILL_LABEL_ALIASES` (118), `_ROLE_LINE_RE` (159),
  `_MONTH_RE` (155).
- Why not reused: all three are extended in place - no new parser is
  introduced. `_MONTH_RE` is reused verbatim by the widened role pattern.

**Verification:**
- `parse_baseline(Baseline_Resume.docx)` returns `warnings == []`.
- `verified.json` regains non-empty `skills_core`, `skills_ai`, and all
  developer roles in `work_history`.
- The 11 currently-failing content tests pass again.

**Result - code half (2026-07-24):**
- `_SKILL_LABEL_ALIASES` gained six keys: `languages & frameworks`,
  `languages and frameworks` -> Core; `ai & automation`, `ai and automation`,
  `automation` -> AI & Tooling. Both `&` and `and` spellings, since a resume
  reformat is exactly when that varies.
- `tests/test_parse_docx.py::test_compound_skill_labels_map_to_buckets` pins
  it against a generic "Jane Dev" fixture (no personal literals - A10 posture).
- Measured on the live baseline: `skills_core` 0 -> 13, `skills_ai` 0 -> 6,
  warnings 12 -> 10. The residual 10 are all role headers.
- Gates: `uv run pytest` 999 passed; `ruff check` clean; `mypy src` clean.

**Agent verification pass (2026-07-25 00:3x):**
- Independently confirmed the above against `Casey_Hsu_Resume.docx` (the
  baseline was renamed mid-phase and is now found via the new
  `resume/locate.py`): `skills_core` 13, `skills_ai` 6, `skills_cms` 6,
  `skills_data_devops` 12, `skills_familiar` 8, `projects` 2, warnings 10.
- A9b guard confirmed live: `jobhunt convert-resume` exits 1 and writes
  nothing.
- The agent had started a competing `_lossy_warnings` guard in
  `convert_resume_cmd.py` before noticing A9b already existed, and
  **reverted it**. Net agent change to that file this pass: none.
- Suite now **3 failures / 1022 passed** (was 11 / 993): `test_parse_docx` x2,
  `test_query_planner` x1 - all downstream of the missing role titles.
  `ruff` clean; `mypy` clean, 78 files.

**STILL UNSAFE TO RUN `scan` / `apply` / `resume`.** The guard blocks a *new*
bad write; it does not repair the existing one. `kb/profile/verified.json` on
disk is still the 22:02 snapshot - 0 core skills, 0 AI skills, 0 projects, and
one "Sous Chef & Team Lead" role. It is repaired only once the role titles land
and `convert-resume` completes successfully.

### Phase A9d - Make the convert-resume guard fail closed (low priority)

**Goal:** Treat any non-benign parse warning as data loss, rather than
allow-listing the words "dropped"/"skipped".

`_dropped_content_warnings` currently matches on two words. A future warning
kind that discards content without using either would pass the guard silently -
the exact failure class A9b exists to prevent. Inverting the polarity (benign
markers listed explicitly, everything else blocks) is a ~5-line change. Only
one benign kind exists today: `"defaulted to education"`.

**Status:** [ ] not started - low priority, no known trigger

### Phase A9c - Make the resume parser format-agnostic

**Goal:** Parse a resume by structure rather than by one document's conventions,
so a restyle or reformat cannot silently drop content.

**Driver:** two resume rewrites in 24h broke the parser twice in different ways
(2026-07-25: six unseen skill labels + title-less role headers; 2026-07-26:
three-cell headers, a descriptor cell in project headers, and every bullet
restyled from "List Paragraph" to "normal"). Each time the failure was silent
data loss, not an error.

**Result (2026-07-26):**
- **Bullets by formatting, not style name.** `_is_list_item` keys on the
  `<w:numPr>` numbering property. The 2026-07-26 restyle renamed every bullet's
  style to "normal" while leaving it a genuine list item; the old
  `style == "List Paragraph"` check reclassified all of them as body text.
  `parse_baseline` now normalises real list items to one canonical label.
- **Skill buckets by keyword inference,** not an exact-label allow-list
  (`_infer_skill_bucket`). Token-based so short keywords cannot match inside
  unrelated words. Familiar-ish labels are tested first: mis-filing an
  "Additional" row into Core would promote academic exposure to claimable
  production skill, the one bucket error the honesty rules treat as fabrication.
- **Unknown labels are kept, not dropped** — assigned to Core with an advisory
  warning worded "assigned" so A9b's data-loss guard does not block the write.
  `_NON_SKILL_LABEL_TOKENS` still drops genuine non-skill rows (Interests,
  Hobbies, Awards); without it an Interests row would file "Chess" as a skill.
  A pre-existing test caught exactly that regression.
- **Role headers by anchoring on the date range,** then splitting what precedes
  it (`_parse_role_header`). Handles 2-cell, 3-cell (location in the employer
  cell), title-less em-dash, and tab-separated shapes. Extra cells rejoin with
  commas so no stray separator survives into `employer` - half the identity key
  the fabrication guard compares on.
- **Project headers rsplit** on the last separator, tolerating a descriptor
  cell (`Name | Descriptor | URL`). Splitting on the first `|` had dropped every
  project.
- **Education classifier** gained `polytechnic|institute|academy` and academic
  detail vocabulary (`capstone|thesis|practicum|coursework|major|minor`).
- Live result: **0 parse warnings**, 4 roles, 1 project, 56 core skills.
  `verified.json` regenerated - the A9 blocker is cleared.
- Suite: **1036 passed**; `ruff` clean; `mypy` clean, 79 files.

**Test-coupling cleanup (partial A12).** Roughly a dozen assertions in
`test_parse_docx.py` pinned exact content of one draft (five named projects, a
specific lead-role title, "GPU optimization (cache, flash attention)"). They
were rewritten as invariants - a project has a clean name, a scheme-stripped
url, and bullets; the Familiar bucket is non-empty and disjoint from Core; the
lead role has a non-empty title and carries "Present". Fixtures remain the real
fix; this only stops an intentional resume edit from reading as a parser
regression.

**Status:** [x] done

**Remaining (document half, owner: Casey):** restore the four role titles in
`Baseline_Resume.docx` using the pipe form, then re-run `convert-resume`.
Suggested titles chosen for lane coverage rather than repeating one label
(full-stack + CMS + Shopify across the three engagements):

```
Full-Stack Developer (Contract) | Atelier Dacko — Custom Jewelry Brand      Apr 2023 – Present
CMS Developer (Contract)        | Neurative AI — SEO AI Agency              Jan 2026 – Apr 2026
Shopify Developer (Contract)    | Geeked Out Goods — Vintage-Gaming Store   Jan 2024 – May 2024
Sous Chef & Team Lead           | JOEY Restaurant Group…, Toronto           Feb 2015 – Nov 2025
```

Note: `Resume_Tailoring_Instructions.md` lines 37-39 currently name Dacko "CMS
/ E-commerce Developer (Contract)". If the full-stack label is adopted, update
that table too so one file stays the source of truth.

**Result - second code pass (2026-07-24), two further silent failures found:**

While swapping a project entry, `projects` parsed as **0** with **zero
warnings** - a silent drop the A9b guard cannot catch, and worse than the noisy
one. Two causes, both fixed:

1. `_SECTION_ALIASES` had `personal/selected/side projects` but not
   **`technical projects`**, the heading actually used. The heading was
   therefore not a section boundary, and because `Sous Chef | JOEY …` *does*
   match the role regex, the heading and every project line were absorbed as
   bullets on the sous-chef role. Added `technical projects`, `key projects`,
   `notable projects`, `open source`, `open-source projects`.
2. `_is_project_header` required `Name | url`; the resume uses
   `Name — Description — url`. Widened to accept the em-dash form, splitting on
   the **last** em-dash and additionally requiring the tail to look like a URL
   (`.` or `/`) - prose bullets in this section routinely contain em-dashes,
   and without the URL test a bullet ending in one word would parse as a
   header. New `_split_project_header` is shared by the predicate and its
   consumer so the two cannot disagree about the split point.

Measured after: `projects` 0 -> 2, each with stack (5) and bullets (2);
warnings back to 10, all `PROFESSIONAL EXPERIENCE`. Two tests added, including
one asserting the role does **not** swallow the projects section.

Gates: `uv run pytest` 1014 passed, 9 failed (all the corrupted-profile set),
2 skipped; `ruff check` clean; `mypy src` clean.

**Status:** [~] code half done; blocked on the document edit

### Phase A9b - `convert-resume` refuses to write a partial profile

**Goal:** Make a lossy parse fail loudly instead of silently writing a broken
`kb/profile/`.

**Files touched:**
- src/jobhunt/commands/convert_resume_cmd.py - `_dropped_content_warnings` +
  guard before the write, `--force` escape hatch
- tests/test_convert_resume_guard.py - new, 5 tests

**Reuse audit:**
- Search terms: `grep -n "parse_baseline\|warnings\|write_verified_json" src/jobhunt/commands/convert_resume_cmd.py`
- Candidates found: the existing warning-print block (line 146) and the
  `[applicant]` missing-fields check that already exits 1.
- Why not reused: the warning block runs *after* `write_verified_json` (line
  124), so it cannot gate the write - the guard has to precede it. The
  applicant check is the right precedent for "exit 1 with an explanation" and
  its shape is followed.

**Result (2026-07-24):**
- Classifier keys on the parser's own lossy phrasing (`dropped` / `skipped`),
  so advisory warnings never block a write. Tested both directions.
- Live proof: `uv run jobhunt convert-resume` now exits 1, lists the 10 dropped
  items, and leaves `kb/profile/` untouched.
- Two tests assert on the written artifact rather than the exit code, because
  `convert-resume` also exits 1 on missing `[applicant]` fields - an unrelated
  pre-existing check the minimal fixture trips. Documented in the test.
- Gates: `uv run pytest tests/test_convert_resume_guard.py` 5 passed; full
  suite 999 passed; ruff + mypy clean.
- **Known gap:** the guard prevents *future* corruption; it does not repair the
  `verified.json` already on disk from the 22:02 run. `kb/profile/` is
  gitignored, so recovery is the A9 document edit + re-run, not a git restore.

**Status:** [x] done

### Phase A10 - Sweep hard-coded personal facts out of the codebase

**Requested 2026-07-24.** Groundwork for other people using this tool: every
personal truth should flow from `convert-resume` -> `verified.json`, never from
a literal in source, prompt, or test. A9 is the proof this matters - the parser
and 11 tests are coupled to one specific person's resume, so another user's
first `convert-resume` silently produces a broken profile with a green-ish
test suite.

**Goal:** Inventory every Casey-specific literal outside the intentionally
personal files, then route each through the verified snapshot or a fixture.

**Scope note:** `Baseline_Resume.docx`, `WORK.md`,
`Resume_Tailoring_Instructions.md`, `kb/profile/`, and `data/` are *supposed*
to be personal - they are the inputs. The sweep targets `src/`, `tests/`,
`kb/prompts/`, `kb/policies/`, `kb/lanes/`, and `kb/seeds/`.

**Files to touch:** determined by the inventory; the phase splits once the
inventory exists (this phase produces the inventory + the split).

**Reuse audit:** deferred to the split - the inventory determines whether
existing fixture helpers cover the replacements.

**Verification:**
- A documented inventory with a per-hit disposition (route via verified.json /
  move to fixture / legitimately personal, leave).
- Follow-up phases filed for each disposition group.

**Result - inventory (2026-07-24):**

Search: `grep -rniE "casey|hsu|joey|dacko|neurative|geeked out|george brown|
416-500|outlook\.com|simbuds|caseyhsu"` over `src/`, `tests/`, `kb/prompts/`,
`kb/policies/`, `kb/lanes/`, `kb/seeds/`. 102 hits in src+kb, ~85 in tests.
Sorted into four dispositions, filed as A11-A14 below.

**D1 - Behavioral coupling (HIGH).** Personal facts hard-coded inside logic or
prompt text. These do not merely read oddly for another user - they *silently
no-op*, because the literal never matches:
- `pipeline/cover_validate.py:481` - `recap_tokens` contains `"george brown"`
- `pipeline/answer.py:118` - `_RECAP_TOKENS` contains `"george brown"`
- `pipeline/interview_prep.py:355` - token loop contains `"george brown"`
- `pipeline/interview_prep.py:776` - prompt names George Brown / Dean's List
- `kb/prompts/cover.md:28`, `answer.md:89` - same school, in prompt text
- `kb/prompts/tailor.md:86` - the full George Brown education line verbatim
- `kb/prompts/cover.md:162` - `sign_off` hard-coded to `"Best,\nCasey Hsu"`
- `kb/prompts/{answer,cover,interview-prep}.md` - "Atelier Dacko" named as the
  canonical anchor project (4 sites)
- `kb/policies/tailoring-rules.md:12` - the Familiar bucket enumerated inline
All of these already exist in `verified.json` (`education`, `work_history`,
`projects`, `skills_familiar`) or `cfg.applicant.full_name`. -> **Phase A11**

**D2 - Tests read live personal data (HIGH).** Six files load
`Baseline_Resume.docx` or `kb/profile/verified.json` from the repo root rather
than a fixture: `test_parse_docx`, `test_query_planner`, `test_audit`,
`test_cover_validate`, `test_analyze_expansion`, `test_setup_wizard`.
Consequence, demonstrated today: editing a personal, **gitignored** document
broke 11 tests across 4 of those files, and CI on another machine would fail
or pass depending on whose resume is checked out. -> **Phase A12**

**D3 - Prompt voice / identity (MEDIUM).** ~40 hits across `kb/prompts/*.md`
and `kb/policies/tailoring-rules.md` of the form "Casey's voice", "Write a
cover letter for Casey", "Casey is an IC engineer". Behaviourally harmless
(the model reads a name it also receives in the payload) but they make the
prompt library one person's. Replace with "the candidate" plus the injected
name. -> **Phase A13**

**D4 - Comments (LOW).** ~30 in `src/`, e.g. `cover_validate.py:291`
("Casey has Express, not these"), `config.py:142`, `apply_cmd.py:578`. No
behavior. They encode assumptions that mislead a maintainer working for a
different user. -> **Phase A14**

**D5 - Leave as-is.** `kb/seeds/gta-employers.toml` curation notes: the file is
documented as a cold-start aid and its comments are dated provenance, which is
worth keeping. `Baseline_Resume.docx`, `WORK.md`,
`Resume_Tailoring_Instructions.md`, `kb/profile/`, `data/` are inputs and are
*supposed* to be personal.

**Adjacent finding, out of scope, filed for the record:** personalization is
not only identity. The tool hard-codes a *geography* (GTA + 100 km, Job Bank
CA, Adzuna CA, `kb/seeds/gta-employers.toml`) and a currency
(`salary_expectation_cad`). A user outside Ontario has a second, larger
problem that this sweep does not touch. Not filed as a phase - it needs its own
plan.

**Status:** [x] done - inventory complete, A11-A14 filed

**Agent verification pass (2026-07-25).** Ran the sweep independently over the
same scope; the inventory above reproduces. Three implementation details it
does not record, all of which change how A11 should be sequenced:

1. **D1 contains one hit that does *not* no-op.** The rest of D1 fails open -
   `"george brown"` simply never matches another user's profile, so a guard
   silently does nothing. `kb/prompts/cover.md:162` is the opposite: it fixes
   `sign_off` to the literal `"Best,\nCasey Hsu"`, nothing downstream rewrites
   it (`render_cover_docx.py:66` renders `cover.sign_off` verbatim), and
   `grep -rn full_name src/jobhunt/pipeline src/jobhunt/gateway` returns
   **nothing** - the applicant's name is never injected into any prompt today.
   So another user's cover letter ships *signed with Casey's name*. Failing
   open is a dormant bug; this one emits wrong output into an employer-facing
   document. **Do it first in A11.**
2. **The substitution mechanism already exists.** `gateway/prompts.py:25`
   does `user_template.format(**vars)` and raises `PipelineError` on a missing
   key. A11b is therefore a parameter change (`{full_name}` in the template +
   one kwarg at the call site), not new plumbing.
3. **Do not "fix" the `cover_validate` overreach watchlist under A14.** The
   Casey-specific *comments* around it (lines 291, 297, 307) are D4 cosmetics,
   but the token list beneath them (`bun`, `hono`, `trpc`, `prisma`, `kotlin`,
   `swift`, `gcp`, `langchain`, `pinecone`, …) is behavioral and is
   **already user-generic**: `cover_validate.py:145-152` suppresses a violation
   when the matched phrase appears in the verified-skill blob, so the list
   self-corrects per profile. Reword the comments; leave the list.

### Phase A11 - Route hard-coded personal facts through the verified snapshot

**Goal:** Replace every personal literal that participates in behavior with a
lookup against `verified.json` or `cfg.applicant`.

**Files to touch:** `pipeline/cover_validate.py`, `pipeline/answer.py`,
`pipeline/interview_prep.py`, `kb/prompts/{cover,answer,tailor,interview-prep}.md`,
`kb/policies/tailoring-rules.md`

**Note:** exceeds the 5-file budget; split at implementation time into
A11a (the three `pipeline/` recap-token sites, one shared helper) and
A11b (prompt/policy text). Recorded rather than silently exceeded.

**Verification:**
- A fixture profile with a different school/employer produces the same guard
  behavior the Casey profile does today.
- `scripts/eval_tailor.py` golden set shows no verdict regressions.

**Result - A11a, the sign-off (2026-07-25):**

Resequenced to run first: every other D1 hit fails *open* (a literal that never
matches simply no-ops), but this one emitted wrong output into an
employer-facing document.

- `kb/prompts/cover.md:162` no longer names a candidate. The rule now points at
  the `name` field of the Verified facts JSON and states that the pipeline
  overwrites the field regardless, so the model is never the authority.
- `pipeline/cover.py` - the verified name was *already* read (for the sign-off
  strip regex) and *already* composed into `default_signoff`, but line 108 used
  it only as `raw.get("sign_off") or default_signoff`. The model's value won,
  and the prompt told it to emit "Best,\nCasey Hsu". Now the verified name wins
  outright when present; the model's value is used only when the profile
  carries no name.
- Chose deterministic override over prompt-instruction, matching the repo's
  structural-enforcement posture (AGENTS.md: honesty enforcement is
  structural). A name is identity, not prose - it should not depend on
  instruction-following.
- Tests in `tests/test_cover_signoff_strip.py`: a profile named "Jane Dev"
  overrides a model emitting "Best,\nCasey Hsu"; a nameless profile still falls
  back. Regression confirmed by `git stash` round-trip - fails without the
  change, passes with it.
- Gates: 66 cover-related tests pass; `ruff check` clean; `mypy src` clean,
  78 files. Full-suite run deferred at Casey's request while the resume is open
  for editing (the 3 known content failures are unrelated to this change).
- Out of scope, deliberately left: the ~10 remaining "Casey" mentions in
  `cover.md` are prompt *voice* (D3 -> A13), not identity emission.

**Result - A11b, the recap tokens (2026-07-25):**

- `pipeline/_recap.py` added: `recap_tokens(verified, *, extra=())` derives
  institution names from `verified.json`'s `education` entries and unions them
  with person-independent markers (`dean's list`, `diploma`).
- Handles every education shape observed or plausible: free-text with an
  em-dash separator (the live format), dict entries
  (`institution`/`school`/`name`), a bare string, and missing/empty.
- Emits both the full name and the short form (`waterloo university` +
  `waterloo`), because people write both. Sorted longest-first so a violation
  message quotes the full name.
- `extra` exists to avoid a silent behavior change: `answer.py` matched
  `"coursework:"` (the resume's literal label) while the other two matched the
  bare word `"coursework"`. Unifying them would have widened one validator as a
  side effect of a refactor, so each call site passes its own marker.
- Wired into all three sites: `answer.py:187`, `cover_validate.py:481`,
  `interview_prep.py:355`. Also fixed `interview_prep.py:777`, a *retry-hint
  prompt string* that named the school in code - now "the school name,
  diploma, honours, or coursework".
- `grep -rniE "george brown" src/` returns **nothing**.
- Regression test asserts the derived set is a superset of each validator's
  previous hard-coded tuple, so no validator lost sensitivity.
- Gates: 122 tests across the affected surface pass (9 new in
  `tests/test_recap_tokens.py`); `ruff check` clean; `mypy src` clean, 79 files.
- Left for A13 (prompt voice, not identity data): `interview_prep.py:783`
  ("Do not claim Casey can start immediately") and the 3 remaining school
  references in `kb/prompts/`.

**Status:** [x] A11a + A11b done; prompt/policy voice remains as A13

**Status:** [ ] not started

### Phase A12 - Decouple the test suite from the live baseline resume

**Result (2026-07-26): closed.** Most of this had already landed - a fictional
`tests/fixtures/profile/verified.json` ("Jane Dev") plus a `verified` fixture in
`conftest.py`, with `test_audit`, `test_cover_validate`, and
`test_analyze_expansion` already pointed at it. Two gaps remained, and a third
had drifted back:

- `test_query_planner.py` still read the live `kb/profile/verified.json`.
  Repointed at the fixture. `test_derive_from_current_baseline` became
  `test_derive_from_fixture_profile`, and its load-bearing assertion is now the
  *exclusion* - Java/Spring Boot sit in `skills_familiar`, so `java developer`
  must never become a query.
- Added `test_seo_query_is_gated_on_work_history_evidence`, which pins a design
  detail the old live-profile assertion obscured: `_has_seo_signal` scans
  work-history **bullets**, not skills rows, so claiming "technical SEO" in a
  skills row does not start searching SEO roles - demonstrated work does. Both
  halves are asserted.
- `test_parse_docx.py` had drifted *away* from its own documented contract
  ("assert it parses cleanly rather than asserting its content"): roughly a
  dozen assertions pinned one draft's content - five named projects, the old
  lead-role title, `GPU optimization (cache, flash attention)`. Rewritten as
  invariants under A9c, restoring the stated design.

The live resume is now touched by exactly the smoke tests that assert it parses
cleanly, which is the real regression signal. `grep` over `tests/` finds no
remaining read of `kb/profile/` or the root resume outside tmp-dir fixtures and
`test_resume_locate` (which tests the locator itself).

Suite: **1037 passed**; `ruff` clean; `mypy` clean, 79 files.

**Status:** [x] done

**Goal:** Point every test at a committed fixture profile instead of the
user's personal `Baseline_Resume.docx` / `kb/profile/verified.json`.

**Files to touch:** `tests/fixtures/profile/` (new: a fixture .docx + its
verified.json), `tests/test_parse_docx.py`, `tests/test_query_planner.py`,
`tests/test_audit.py`, `tests/test_cover_validate.py`,
`tests/test_analyze_expansion.py`, `tests/test_setup_wizard.py`

**Note:** also exceeds the file budget; split per test file at implementation
time. Keep **one** deliberately-live smoke test that parses the real baseline
and asserts `warnings == []`, so a real regression still surfaces - just not
as 11 opaque assertion failures.

**Verification:**
- `git stash` the baseline resume; the suite still passes.
- The 11 failures from 2026-07-24 become impossible by construction.

**Correction to the A10 inventory:** the coupling was **4 files, not 6**.
`test_analyze_expansion` and `test_setup_wizard` write their own
`verified.json` into tmp and merely *mention* the filename - they were false
positives from a filename-only grep.

**Result - A12a (2026-07-24):**
- `tests/fixtures/profile/verified.json` added: one fictional candidate ("Jane
  Dev", Northwind/Contoso/Fabrikam) rich enough to serve both suites - includes
  AWS **and** Azure, which the peer-broadening dedupe test needs.
- `tests/conftest.py` now owns the shared `verified` fixture. The two local
  copies in `test_audit` / `test_cover_validate` are deleted, along with their
  `if VERIFIED_PATH.is_file()` branches - that branch was the actual bug: the
  live file won even when corrupted, and the stub only ran when it was absent.
- **68 tests in those two files now pass**, including all 8 that were red.
- `test_audit_alignment_flags_drift_between_resume_and_cover` re-anchored onto
  fixture-world names. **Proved non-vacuous**: with the HubSpot/Shopify drift
  removed the test fails, with it present it passes - so it still exercises the
  alignment check rather than passing by accident on a profile that no longer
  contains the old anchor.
- `test_parse_docx.BASELINE` now resolves via
  `resume.locate.find_baseline_resume()`, converting A15's two **silent skips**
  back into real guards. They fail right now, correctly, on the 10 pending
  role-header warnings.
- Suite: **1022 passed, 3 failed** (was 9 failed). `ruff check` clean after
  removing imports the deletions orphaned; `mypy src` clean.

**Remaining for A12b** (2 genuinely-coupled tests left):
- `test_parse_docx::test_parse_baseline_positioning_and_atomic_skills` asserts
  *content* of the live resume ("Dawn survives the parse", lead-role retitle).
  Needs a fixture `.docx` so content assertions leave the personal document.
- `test_query_planner::test_derive_from_current_baseline` reads the live
  `verified.json` and asserts specific derived queries. Split it: fixture-based
  coverage of the derivation *logic*, plus an explicitly-marked config check
  that the user's own profile yields the queries they expect.

`test_parse_baseline_round_trip` **stays live by design** - it is the one
deliberate smoke test asserting the real resume parses cleanly.

**Status:** [~] A12a done; A12b outstanding

### Phase A13 - Depersonalize prompt and policy voice

**Result (2026-07-26): attempted, measured, REVERTED.** The substitution was
made and then backed out because `scripts/eval_tailor.py` showed it degraded
output. Recorded in full because the finding outlives the attempt.

Change made: 43 occurrences of `Casey`/`Casey's` -> `the candidate`/`the
candidate's` across `kb/prompts/{score,tailor,cover,answer,interview-prep}.md`
and `kb/policies/{tailoring-rules,authoring}.md`, plus sentence-start
recapitalisation and four gendered pronouns (`his work history`, `his WordPress
work`) -> `their`. A leftover `the candidate Hsu` was cleaned up. Final sweep
for `casey|hsu|his|him` over both directories returned clean, all prompts still
loaded with intact frontmatter/schema, and the suite stayed at 1037 passed.

**Then the golden-set eval, run before and after per the README:**

```
                       score  verdict  cov%   attempts
before (named)            82   revise   67     1-2/1-3
after  ("the candidate")  79   revise   50     2/1
```

Three samples per side, all identical within side - deterministic, not
run-to-run variance. `git diff` confirmed the only delta was the name
substitution, so the comparison was clean.

**Finding: the model grounds better on a concrete name than on an abstract
referent.** "Casey's verified facts" binds the instruction to the supplied
profile in a way "the candidate's verified facts" does not, and 17 points of
keyword coverage is an ATS-match cost far larger than the cosmetic benefit.
This is exactly the regression the eval harness exists to catch, and it would
not have shown up in any unit test - the suite was green throughout.

**Reverted** via `git checkout kb/prompts kb/policies`; a confirming eval run
returned 82 / 67%.

**Status:** [x] attempted and reverted - superseded by A13b

### Phase A13b - Depersonalize by injecting the real name

**Goal:** Get the depersonalization benefit without the grounding loss, by
substituting the *configured applicant's* name into prompts rather than
replacing it with an abstract noun.

Another user then sees their own name where this repo currently hard-codes
one - the same posture A11a already established for the cover-letter sign-off,
which derives from `verified.json` rather than a literal.

**Files to touch:** `src/jobhunt/gateway/prompts.py` (a `render_system`
alongside `render_user`), the five prompt files, and the call sites in
`pipeline/{score,tailor,cover,answer,interview_prep}.py`.

**Note:** exceeds the 5-file budget and adds one public interface; split at
implementation time into A13b-i (gateway + one pipeline as a walking skeleton,
verified by eval) and A13b-ii (the remaining four).

**Feasibility already checked:** only `answer.md`'s system half contains a
literal brace pair, so `str.format` needs escaping in exactly one file. The
other four are brace-free.

**Verification (non-negotiable):** `scripts/eval_tailor.py` must return to
82 / 67% on `shopify-developer` before this is called done. A green unit suite
is not sufficient evidence - A13 proved that.

**Result - A13b-i, the walking skeleton (2026-07-26):**
- `Prompt.render_system(**vars)` added beside `render_user`. Returns the system
  half unchanged when it contains no `{`, so it is safe to call on every prompt
  whether or not that prompt takes variables.
- `kb/prompts/tailor.md`: 4 hard-coded names -> `{candidate_name}`. Chosen as
  the skeleton because tailoring drives the coverage metric the regression
  showed up in.
- `pipeline/tailor.py`: `_candidate_name(verified)` derives the interpolated
  value from the loaded profile. It returns the **first name in prose case**
  because resume headers are all-caps (`CASEY HSU`) and the prompts were
  written around a bare first name - reproducing that surface form exactly is
  the whole point.
- **Proved byte-identical:** the rendered system prompt was diffed against the
  pre-change `kb/prompts/tailor.md` reconstructed from `git show HEAD:` -
  identical, so no behaviour change was possible by construction.
- **Golden eval: 82 / 67%**, matching the pre-A13 baseline exactly. The
  depersonalisation is now free.
- `tests/test_prompt_name_injection.py` (7 tests): substitution, the no-op path,
  the missing-variable error, all-caps downcasing, the no-name fallback, and a
  regression guard that the shipped prompt never re-acquires a literal name.
- Suite **1044 passed**; `ruff` clean; `mypy` clean, 79 files.

**Remaining - A13b-ii:** the same treatment for `score.md`, `cover.md`,
`answer.md`, `interview-prep.md` and `kb/policies/tailoring-rules.md`.
`answer.md`'s system half holds the one literal brace pair in the library and
needs `{{`/`}}` escaping. `tailoring-rules.md` is injected *and* feeds the score
prompt hash, so editing it re-scores every job - worth batching with any other
hash-affecting change.

**Status:** [~] A13b-i done and eval-verified; A13b-ii outstanding

**Goal:** Replace "Casey" with "the candidate" plus the injected name across
`kb/prompts/` and `kb/policies/`.

**Verification:** `scripts/eval_tailor.py` golden set shows no verdict or
score regressions vs. a pre-change run.

**Status:** [ ] not started

### Phase A14 - Depersonalize source comments

**Goal:** Rewrite the ~30 `src/` comments that assert facts about one specific
user. No behavior change; `git diff --stat` should show comments only.

**Status:** [ ] not started - lowest priority

### Phase A15 - Discover the baseline resume by filename pattern

**Requested 2026-07-24.** The path is hard-coded to `Baseline_Resume.docx` in
four places. Renaming the file to `Casey_Hsu_Resume.docx` (done at 22:00) broke
`convert-resume` outright - it now exits file-not-found, and `setup` would too.

**Goal:** Locate the baseline resume by matching any root-level `.docx`/`.pdf`
whose filename contains "resume", instead of requiring one exact name.

**Files to touch:**
- src/jobhunt/resume/locate.py - new
- src/jobhunt/commands/convert_resume_cmd.py - default via the locator
- src/jobhunt/commands/setup_cmd.py - same (line 56)
- tests/test_resume_locate.py - new

**Functions to add/change:**
- `resume.locate.find_baseline_resume(root, explicit=None)` - add - returns the
  chosen path; raises a `PipelineError` naming candidates when none match.

**Selection rule (deterministic, must be stated in output):** an explicit
`--docx` always wins. Otherwise, among root-level files matching
`*resume*.{docx,pdf}` case-insensitively: prefer a name containing "baseline",
then `.docx` over `.pdf`, then most-recently-modified. Word lock files (`~$…`)
are excluded. The chosen path is always echoed, because silently picking one of
several resumes is worse than picking the wrong one loudly.

**Search is non-recursive**, deliberately: `data/resumes/` holds generated lane
resumes (`Casey_Hsu_Resume_AI_Automation.docx`) and `data/applications/<id>/`
holds tailored per-application copies. A recursive search would pick a
generated artifact as the source of truth for regenerating itself.

**Reuse audit:**
- Search terms: `grep -rn "Baseline_Resume.docx" src/`
- Candidates found: `setup_cmd.py:56`, `convert_resume_cmd.py:127` (the typer
  default), plus docstrings at `convert_resume_cmd.py:3,26`,
  `parse_docx.py:1`, `interview_prep.py:1073`.
- Why not reused: there is no existing locator - the literal is repeated. This
  phase introduces the single source and the two behavioral sites call it.

**PDF caveat:** `pyproject.toml` declares `python-docx` only; nothing can read
a PDF today. Discovery recognizes `.pdf` so the file is *found* and the failure
is explicit, but parsing needs a new dependency - a risky-tier decision, asked
separately rather than slipped in.

**Verification:**
- Live: `jobhunt convert-resume` finds `Casey_Hsu_Resume.docx` with no flag.
- Test: name variants, `.pdf` preference order, lock-file exclusion, and the
  no-match error.

**Result (2026-07-24):**
- `resume/locate.py` added (`find_baseline_resume`, `describe_choice`); wired
  into `convert_resume_cmd` (typer default is now `None`) and
  `setup_cmd._step_resume_present`.
- Live: `jobhunt convert-resume` with no flag prints
  `resume: Casey_Hsu_Resume.docx` and proceeds - the file-not-found is gone.
  A9b's guard then still blocks the write, correctly, since titles are missing.
- 13 tests in `tests/test_resume_locate.py`, including the non-recursive
  guarantee (a `data/resumes/` lane resume must never be picked as the source
  of truth for regenerating itself).
- PDF is *discovered* but rejected at parse time with an actionable message;
  adding a PDF reader is a dependency decision, raised separately.
- Gates: `uv run pytest` 1012 passed, 9 failed, 2 skipped; `ruff check` clean;
  `mypy src` clean, 78 files.

**Side effect worth acting on (feeds A12).** The failure count moved 11 -> 9
not because anything was fixed, but because
`tests/test_parse_docx.py` computes `BASELINE = REPO_ROOT /
"Baseline_Resume.docx"` and is `skipif(not BASELINE.is_file())`. The rename
turned two real regression guards into **silent skips**:

```
SKIPPED [1] tests/test_parse_docx.py:45: baseline .docx not present
SKIPPED [1] tests/test_parse_docx.py:120: baseline .docx not present
```

A skip is worse than a failure here - it is invisible. Those two tests should
call `find_baseline_resume()` instead of hard-coding the name. Folded into A12
rather than done here, since `tests/test_parse_docx.py` is outside this phase's
declared surface.

**Status:** [x] done

### Phase A16 - Make the KB self-sufficient without WORK.md / Resume_Tailoring_Instructions.md

**Requested 2026-07-24.** "Ensure we can reliably pull everything we need
without having to use `Resume_Tailoring_Instructions.md` and `WORK.md`."

**Finding that reframes the request: the runtime pipeline already does not read
either file.** `grep -rn "WORK.md\|Resume_Tailoring_Instructions" src/` returns
only comments and docstrings (`render_docx.py:3`, `parse_docx.py:5,89,90,541`).
The only policy input the pipeline loads is `kb/policies/tailoring-rules.md`
(`tailor.py:76`, `score.py:44`, and `score.py:394`'s prompt-hash list). So the
dependency is in the **authoring** workflow - what a human or agent must read
to do resume work - not in the code path.

**Gap analysis:**

`Resume_Tailoring_Instructions.md` (300 lines) splits cleanly:
- §2 "Verified Facts About Casey" (~97 lines: work history, skills, certs,
  coursework, quantified outcomes, projects, "what Casey has NOT done") -
  **personal data that duplicates `verified.json`**, and can drift from it.
- §1, §3, §4, §7, §9, §10 (~110 lines: inputs to demand, tailoring workflow,
  what's OK to adjust, common pitfalls, output format, cover notes) -
  **generic tool policy that is NOT in the runtime mirror.**
- §5, §6, §8 - already mirrored into `kb/policies/tailoring-rules.md`.

The 109-line mirror therefore covers roughly half the policy. The uncovered
half is the part agents most need and most often re-derive.

`WORK.md` (342 lines) is a deliberate **superset** of the resume: per-role long
form, projects not on the baseline (macOS-on-KVM, hybrid coding agent), full
coursework, plus "how to use X in tailoring" guidance. None of it reaches the
pipeline today.

**Proposed shape:**
1. `kb/policies/authoring.md` - the ~110 lines of uncovered policy, agent-facing
   and **not** prompt-injected (injecting it would cost tokens for rules the
   model does not act on).
2. `kb/profile/supplemental.json` - the WORK.md facts that are genuinely extra,
   as structured data next to `verified.json`.
3. Root docs become derived or deleted; `AGENTS.md` documentation map updated.

**Open decision - must be answered before coding.** `verified.json` currently
means exactly "what is on the baseline resume", and the fabrication guard
checks generated claims against it. Promoting WORK.md's extra projects into the
verified set would let the tailor surface work that is *not* on the baseline.
That may be wanted (WORK.md already annotates entries "on the baseline as of
2026-06-23", implying deliberate curation) but it **changes the honesty
guarantee**, so it is not a call to make silently. Options:
 (a) supplemental facts are agent-reference only, never fed to the tailor -
     guarantee unchanged, richness preserved for humans;
 (b) supplemental facts are merged into the verified set - richer tailoring,
     but "verified" no longer means "on the resume";
 (c) supplemental is a separate prompt input with its own weaker guard.

**Verification:**
- `grep -rn "WORK.md\|Resume_Tailoring_Instructions" src/ kb/` returns nothing
  load-bearing.
- An agent given only `AGENTS.md` + `kb/` can complete a tailoring task.
- `scripts/eval_tailor.py` golden set shows no verdict regressions.

**Decision (2026-07-24): option (a).** Supplemental long-form facts are
agent-reference only and are never fed to the tailor. `verified.json` continues
to mean exactly "what is on the baseline resume", so the fabrication guard's
guarantee is unchanged.

**Result - A16a, policy extraction (2026-07-24):**
- Discovery that sharpens the phase: **both root docs are untracked**
  (`.gitignore` lines 14-18 cover `kb/profile/`, `data/`, `*.docx`, `*.pdf`,
  `WORK.md`; `Resume_Tailoring_Instructions.md` is simply not committed). So a
  fresh clone already had **no** copy of the ~110 lines of generic policy. The
  gap was not theoretical - it was total, for every user but this one.
- `kb/policies/authoring.md` added (tracked): required inputs, the 10-step
  tailoring workflow, the may-adjust table, the 12-point pre-delivery pitfall
  audit, output/delivery rules, and cover-note rules. Extracted **and
  depersonalized** - `grep -cniE "casey|hsu|george brown|gbc|dacko|neurative|
  joey|geeked"` returns **0**. Facts are sourced from `verified.json`.
- Verified non-injected: the only policy loads are
  `pipeline/tailor.py:76` and `pipeline/score.py:44`, both naming
  `tailoring-rules.md` explicitly, and `score.py:394`'s prompt-hash list is a
  fixed tuple. No glob over `kb/policies/`, so adding a file there cannot
  silently enter a prompt or change the prompt hash.
- `kb/README.md` updated: documents the injected/not-injected split and drops
  the instruction to treat the untracked root doc as source of truth.

**Result - A16b, migrate then delete both root docs (2026-07-25):**

Casey asked for both root docs to be removed. Both were **untracked and not
gitignored**, so `rm` would have been unrecoverable - git never held them.
Migrated first, deleted second.

- `WORK.md` -> `kb/profile/work-long-form.md` (whole-file move, 342 lines).
  All of it is supplemental personal reference and none of it was duplicated
  elsewhere, so a move preserves everything with zero transcription risk.
- `Resume_Tailoring_Instructions.md` §2 -> `kb/profile/verified-notes.md`
  (113 lines, extracted by line range, not retyped). Only §2 was unique: §1,
  §3, §4, §7, §9, §10 already live in `kb/policies/authoring.md` (A16a) and
  §5, §6, §8 in `kb/policies/tailoring-rules.md`. Keeping the whole file would
  have left a second copy of the policy to drift.
  Preserved and spot-checked: the canonical role-title table (the A9 source),
  quantified outcomes, and "What Casey has NOT done" - the negative facts that
  exist nowhere else.
- `Resume_Tailoring_Instructions.md` **deleted**. Repo root is now
  `AGENTS.md`, `IMPLEMENT.md`, `PLAN.md`, `README.md` only.
- Both new files confirmed covered by `.gitignore:14` (`kb/profile/`), which
  keeps A16 decision (a) honest: personal, untracked, never tailor input.
- Re-pointed 8 code/test citations + 5 doc references: `render_docx.py:3`,
  `parse_docx.py:5,89,541` (541 *writes* a pointer into generated
  `kb/profile/projects.md`, so a stale path would have propagated),
  `test_query_planner.py:30`, `test_parse_docx.py:82,101`,
  `kb/policies/tailoring-rules.md:3`, `README.md` (data-layout table + doc
  map), `AGENTS.md:175`, `PLAN.md:167`.
- Remaining mentions are deliberate history: `IMPLEMENT.md` phase records and
  `authoring.md:182`'s provenance line.

**Side effect worth knowing.** `kb/policies/tailoring-rules.md` is in
`score.prompt_hash`'s input tuple (`score.py:394`) *and* is injected verbatim on
every tailor call (`tailor.py:76`). Editing its header therefore invalidates
every existing score - the next `scan` re-scores. A first draft of that edit
added a 6-line historical footnote; it was trimmed to 3 lines because injected
text costs tokens on every call. (A re-score is coming regardless: `verified.json`
is also a hash input and changes when the resume is fixed.)

- Gates: `ruff check` clean; `mypy src` clean, 79 files. Tests unchanged at the
  3 known content failures from the missing role titles.

**Status:** [x] A16a + A16b done

## Completed

### Phase R1 - Lane base resumes via `jobhunt resume` (2026-07-07)

**Goal:** Three regenerable lane-focused base resumes (AI Automation, CMS/E-com, Technical SEO) for manual channels, under the full-stack repositioning.

**Files touched:**
- kb/profile/verified.json + skills.md + resume.md + Baseline_Resume.docx - full-stack summary; new grounded skills "AI automation agents (Claude API, Ollama)" and "Technical SEO (on-page, Core Web Vitals, PageSpeed)" (round-trips through convert-resume byte-identically)
- kb/policies/tailoring-rules.md + kb/prompts/tailor.md rule 6a + Resume_Tailoring_Instructions.md + WORK.md - baseline identity is now full-stack with CMS/E-com depth; lane labels allowed when verified-grounded; Neurative AI real name in use
- kb/lanes/{ai-automation,cms-ecommerce,technical-seo}.md - new pseudo-JD briefs (bodies > thin_jd_chars)
- src/jobhunt/commands/resume_cmd.py + cli.py - new `jobhunt resume --focus ai|cms|seo|all`; synthetic Job per lane -> tailor_resume_with_retry -> render_docx; output data/resumes/
- tests/test_resume_cmd.py - brief parsing (real kb files), synthetic Job, CliRunner render with stubbed tailor

**Reuse audit:** tailor_resume_with_retry, render_docx.render, ensure_profile, apply_cmd contact-line pattern all reused; no new pipeline code.

**Verification:**
- pytest: 919 passed (8 new)
- `jobhunt resume --focus all` live: 3 DOCX rendered; SEO lane recovered clean on retry 2 (fabrication guard exercised)
- convert-resume round-trip after the docx edit: verified.json byte-identical

**Status:** [x] done

### Phase R2 - Two-page full-truth Baseline_Resume.docx (2026-07-07)

**Goal:** Expand the baseline into a parse-ready full-truth master so the tailor selects from richer verified facts (page count of the baseline is irrelevant; the one-page rule binds outputs only).

**Files touched:**
- Baseline_Resume.docx - 46 -> 53 paragraphs: split Dacko migration + Neurative CRM/launch bullets; 2-3 bullets per project (facts sourced from WORK.md Section 1); Core row restored to `React (Redux, React Native)` + Sass; CMS row gained Shopify App Development + Google Tag Manager (Stripe stays off, in-progress)
- kb/profile/* - regenerated via convert-resume; diff verified as additions-only
- Resume_Tailoring_Instructions.md §1 + §2, WORK.md - recorded the rework per the routing rule

**Reuse audit:** parse_docx already supports multi-bullet roles/projects; no code change.

**Verification:**
- convert-resume diff vs pre-change verified.json: intended additions only
- pytest: 919 passed
- `jobhunt resume --focus all` live: 3 lanes clean (SEO recovered on retry 2); portfolio resume.pdf refreshed at 1 page

**Status:** [x] done

