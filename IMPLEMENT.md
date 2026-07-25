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

**Status:** [ ] not started - blocked on A1-A7 and on real application volume

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

**Status:** [ ] not started - blocked on A9 (the parser fix changes the surface)

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

