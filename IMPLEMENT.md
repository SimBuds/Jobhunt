# IMPLEMENT.md — Execution engine

Pillar 4 of the documentation architecture defined in [AGENTS.md](AGENTS.md).
This file is the granular, phase-by-phase breakdown of in-flight work. Agents
**read** this at the start of every phase (Phase 1 and Phase 3 of the Workflow
Contract) and **write** to it as part of the Definition of Done — checking off
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

## Lane-coverage work (2026-06-08)

Widen Jobhunt coverage inside Casey's specialist lane (E-Commerce / Headless CMS
+ Solutions/Implementation Engineer). See `personal/Casey_Action_Plan.md` for the
strategy context.

### Phase C1 — Surface Solutions/Implementation Engineer roles in the Adzuna planner

**Goal:** Emit `solutions engineer` + `implementation specialist` Adzuna queries for CMS profiles, the lane's second job family the planner never surfaced.

**Files to touch:**
- src/jobhunt/ingest/_query_planner.py — add a skills_cms-gated `_CATEGORY_TRIGGERS` entry; bump `derive_adzuna_queries` default `cap` 10 → 12 so the additions don't truncate shopify/hubspot queries.
- tests/test_query_planner.py — add `test_solutions_eng_queries_gated_on_skills_cms`; update `test_derive_from_current_baseline` cap assertion (10 → 12) + required queries.

**Functions to add/change:**
- _query_planner._CATEGORY_TRIGGERS — change — add the solutions-eng trigger.
- _query_planner.derive_adzuna_queries — change — cap default 10 → 12.

**Reuse audit:**
- Search terms: `grep -rn "solutions engineer\|implementation\|_CATEGORY_TRIGGERS" src/`
- Candidates found: existing `_CATEGORY_TRIGGERS` cms/ai/seo trigger pattern.
- Why not reused as-is: no existing solutions-eng query; reused the trigger shape rather than adding a new mechanism.

**Verification:**
- `uv run pytest tests/test_query_planner.py -q` green (new test fails pre-change).
- `derive_adzuna_queries(live verified.json)` contains both new queries and still retains `shopify developer`.

**Status:** [x] done

### Phase C2 — Re-curate the GTA seed list toward in-lane employers

**Goal:** Replace generic GTA tech seeds with Shopify-agency / MarTech-SaaS employers that hire the lane.

**Files to touch:**
- scripts/verify_seeds.py — edit the `CANDIDATES` dict with in-lane candidate slugs, run it.
- kb/seeds/gta-employers.toml — paste the verified TOML block; add a 2026-06-08 re-curation comment.

**Functions to add/change:** none (data + script-driven).

**Reuse audit:**
- Search terms: `grep -rn "verify_seeds\|_probe_one" scripts/ src/`
- Candidates found: `scripts/verify_seeds.py` + `discover.probe._probe_one`.
- Why not reused: it IS the reuse — no new code, only candidate data.

**Verification:**
- verifier prints `ok` + job counts per verified slug.
- `jobhunt config seed --apply` then `jobhunt scan --no-discover` shows new boards contributing.

**Outcome (2026-06-08):** verified live and added in-lane boards — valtech (126), contentful (51), storyblok (8) on greenhouse; sanity (30) on ashby. Generic-AI shops the verifier surfaced (cohere/harvey/mercor/sentry) deliberately excluded per the action plan's defer-AI-roles rule. `config seed --apply` wrote +5 greenhouse / +1 ashby to live config (backup at config.toml.bak; inline comments dropped on write).

**Status:** [x] done
