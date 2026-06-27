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

## In-flight plan: drafted applications remain applyable

User request: if the submit prompt is cancelled or answered `no`, the job
should still be eligible for another apply run. Only an explicit submitted or
withdrawn lifecycle state should remove it from normal apply targeting.

### Phase 1 - Keep drafted jobs eligible for apply targeting

**Goal:** Treat `drafted` application rows as still eligible in normal apply target selection.

**Files to touch:**
- `src/jobhunt/commands/apply_cmd.py` - update `--top`, `--best`, and stretch selection SQL so `drafted` rows are eligible.
- `src/jobhunt/commands/list_cmd.py` - update the default apply-target query so drafted rows still appear as normal targets.
- `tests/test_apply_selection.py` - add focused tests for drafted, applied, and withdrawn selection behavior across apply and list target queries.
- `README.md` - document that answering `no` leaves a drafted row which remains reapplyable, while `withdrawn` removes it from default targeting.
- `IMPLEMENT.md` - track phase state and verification results.

**Functions to add/change:**
- `jobhunt.commands.apply_cmd._unapplied_top_query` - change - include rows with no application or status `drafted`.
- `jobhunt.commands.apply_cmd._resolve_interactive` - change - apply the same eligibility rule to the stretch query.
- `jobhunt.commands.list_cmd._query` - change - apply the same eligibility rule to `default_apply_targets`.
- `tests.test_apply_selection.test_apply_top_includes_drafted_but_excludes_submitted_states` - add - proves apply selection keeps drafts and excludes submitted states.
- `tests.test_apply_selection.test_list_default_targets_include_drafted_but_exclude_submitted_states` - add - proves the default list target view matches apply eligibility.

**Reuse audit:** (per Reuse-First Rule in AGENTS.md)
- Search terms: `rg -n "a\\.id IS NULL|status = 'drafted'|default_apply_targets|_unapplied_top_query" src tests README.md PLAN.md`
- Candidates found: `apply_cmd._unapplied_top_query`, the inline stretch SQL in `apply_cmd._resolve_interactive`, `list_cmd._query` with `default_apply_targets`, and `scan_cmd` cleanup for status `drafted`.
- Why not reused: these are existing command-specific SQL surfaces rather than a shared query helper. The phase changes them in place and does not introduce a new production helper.

**Verification:** (<= 3 bullets)
- `uv run pytest tests/test_apply_selection.py`
- `uv run pytest tests/test_apply_pipeline_e2e.py tests/test_list_filters.py`
- `uv run ruff check src/jobhunt/commands/apply_cmd.py src/jobhunt/commands/list_cmd.py tests/test_apply_selection.py`

**Status:** [x] not started | [ ] in progress | [ ] done
