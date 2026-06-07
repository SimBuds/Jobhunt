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
