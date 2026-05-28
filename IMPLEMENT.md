# IMPLEMENT.md — Execution engine

Pillar 4 of the documentation architecture defined in [AGENTS.md](AGENTS.md).
This file is the granular, phase-by-phase breakdown of in-flight work. Agents
**read** this at the start of every phase (Phase 1 and Phase 3 of the Workflow
Contract) and **write** to it as part of the Definition of Done — checking off
completed phases and logging deferred work as new phases. Never leave a plan in
conversational context alone; it lives here.

## Current state

No active plan. The "Post-audit fixes" plan is complete — summary below.
**Next task queued:** audit of `jobhunt scan --limit 200` output (to be
populated when the run is in hand).

### Completed: Post-audit fixes (2026-05-27)

Addressed the issues surfaced by the 2026-05-27 four-application apply --url
audit (manual:db425a17 OCR, manual:b7a1e9b9 AI Developer, manual:704724fe
Full Stack, manual:4bcf846a Full Stack/Electron).

- Phase 1: **done** — `Resume.docx` summary swapped in place via python-docx (formatting preserved); `Resume.docx.bak` snapshot saved; `jobhunt convert-resume` parsed cleanly (4 roles, 29 core skills, 6 familiar); new summary is 86 words.
- Phase 2: **reverted by user** — after one-page sizing iteration, Casey manually removed the jobhunt role from `Resume.docx`. Current `verified.json` is back to 4 roles. Phase 1 summary still in place.
- Phase 3: **done** — added `HARD_COVERAGE_FLOOR_PCT=50` to `pipeline/audit.py`; verdict ladder escalates sub-50% coverage to `block`; 3 new tests; full suite 675 passed; AGENTS.md §1 updated.
- Phase 4: **done** — added `_OVERREACH_PATTERNS` to `pipeline/cover_validate.py` catching framing-level capability claims; new violation rule_id `unverified_capability` wired through `categorize_violation`; 6 new tests; full suite 681 passed; AGENTS.md §2 updated.

**Inherited decisions (this session):**
- AGENTS.md restructured into portable workflow contract + project rules
  (committed earlier this session).
- IMPLEMENT.md established as pillar-4 execution engine.
- `include_senior_roles=false` restored in `~/.config/jobhunt/config.toml`
  after the post-reset regression (Casey is <3 YoE; senior band always
  declines at score).
- Database wiped (jobs/scores/applications/answers) but `kb/profile/`
  preserved.

## Active plan — Post-audit fixes

Walking-skeleton bias: Phase 1 ships the single highest-leverage fix (the
baseline summary) — it propagates to every future application without any
code change. Phases 2–4 thicken: a new project entry, then two code-side
guards.

### Phase 1 — Rewrite the baseline `Resume.docx` summary to lead software/AI-forward

**Goal:** Replace the verified baseline summary so weak hedging language and culinary-led framing stop propagating into every tailored application.

**Why this is first:** Audit found 3 of 4 tailored resumes carried the verbatim baseline summary, including "applied interest in AI/generative search readiness" and a one-third-summary culinary paragraph — landing in an *OCR document-intelligence* cover where it was wildly off-target. Fixing the source kills the symptom in every future run; no code change required.

**Files to touch:**
- `Resume.docx` — manual edit of the summary paragraph (user-edited; I supply paste-ready text)
- *(regenerated)* `kb/profile/verified.json`, `kb/profile/resume.md`, `kb/profile/skills.md` — output of `jobhunt convert-resume`, not hand-edited

**Functions to add/change:** none — source-data change only.

**Reuse audit:** N/A — this is content, not code. The honesty system already enforces that any tailored summary must derive from `verified.summary`, so the rewrite must remain factually accurate (Casey *is* running local LLMs on a 10 GB 3080 with Ollama + qwen-custom; that's verifiable from this repo).

**Verification:**
- Run `jobhunt convert-resume` and confirm `kb/profile/verified.json` contains the new summary, no parse errors.
- Run `jobhunt apply --url <any AI-adjacent JD> --no-browser` and inspect the rendered `tailored-resume.json` summary — should lead with AI/software, not culinary.
- Spot-check the rendered `Casey_Hsu_Resume.docx` opens cleanly and the summary fits in the existing one-page budget.

**Status:** [x] **done** 2026-05-27. Updated `Resume.docx` paragraph[3] in place via python-docx run-swap (formatting preserved); `Resume.docx.bak` left as safety net. `jobhunt convert-resume` reports `4 role(s); 29 core skills; 6 familiar` with no parse errors. `kb/profile/verified.json` summary now reads as the drafted text (86 words). Tailor-side spot-check (verification bullet 3) deferred — observable in next `apply --url` run.

**Draft summary (paste into the Summary block of `Resume.docx`, replacing the current paragraph):**

> Full-Stack Developer & Contentful Certified Professional with 2+ years of professional client experience. Builds and operates local LLM pipelines on consumer hardware — running quantized Ollama models with KV-cache and flash-attention tuning to drive structured, deterministic AI tooling. Ships custom CMS and e-commerce storefronts across Shopify, HubSpot, and WordPress, with hands-on platform migrations, performance tuning (PageSpeed 90+, 30% load-time cuts), and CI/CD discipline. Nine years of prior team-leadership in a culinary career — budgets, vendors, cross-functional ops — now applied to stakeholder communication and project ownership.

**Honesty trace (every claim → verified source):**
- "local LLM pipelines on consumer hardware, quantized Ollama, KV-cache, flash-attention tuning" → `skills_ai`: Ollama + GPU optimization (cache, flash attention); AGENTS.md hardware-context section documents the q5_0 KV cache + Q4_K_M weights on the 10 GB 3080.
- "structured, deterministic AI tooling" → AGENTS.md gateway design (JSON-schema-enforced calls) + audit pipeline (LLM-free, deterministic).
- "Shopify, HubSpot, WordPress; platform migrations; PageSpeed 90+, 30% load-time cuts; CI/CD" → existing `work_history` bullets (Atelier Dacko + AI Agency).
- "Nine years culinary team leadership; budgets, vendors, cross-functional ops" → existing `work_history` (Sous Chef & Team Lead, 2015–2024).

---

### Phase 2 — Add `jobhunt` as a project entry in `Resume.docx`

**Goal:** Give the tailor a fourth, AI-native work anchor so covers stop forcing the HubSpot/PageSpeed anecdote into AI-role middle paragraphs.

**Why:** Audit found HubSpot + 30% PageSpeed appears in 3 of 4 covers and GitHub Actions CI in 3 of 4 — your verified bullet pool is only ~7 bullets across 3 dev roles, so the cover writer has nowhere else to pull from. Adding `jobhunt` (local-LLM pipeline, Ollama + qwen-custom, structured JSON gateway, deterministic validation, KV-cache GPU tuning) gives every future AI cover a genuine, current anchor and adds a Python/AI accomplishment to the resume itself. Honest because it's real, ongoing, and shipping in this repo.

**Files to touch:**
- `Resume.docx` — add a new role/project entry with 2–3 bullets (I supply paste-ready text; bullets must respect the no-fabrication rules)
- *(regenerated)* `kb/profile/verified.json`, `kb/profile/work-history.md`

**Functions to add/change:** none — source-data change.

**Reuse audit:** N/A — content.

**Verification:**
- `jobhunt convert-resume` parses the new role into `work_history` without error; `verified.json` shows 4 work entries.
- `jobhunt apply --url <any AI/LLM JD> --no-browser` produces a cover whose middle paragraph anchors on the `jobhunt` tool (Ollama / KV-cache / structured pipeline) rather than HubSpot.
- Rendered `Casey_Hsu_Resume.docx` still fits one page — the shrink-ladder may drop the new role's weakest bullet on tight JDs (acceptable).

**Status:** [x] **done** 2026-05-27. Inserted role title + 3 bullets after the Atelier Dacko block in `Resume.docx` (paragraphs 15–18) by cloning the existing role-title and bullet XML to preserve style/list-formatting. `Resume.docx.bak2` saved alongside as rollback. `jobhunt convert-resume` reports `5 role(s); 29 core skills; 6 familiar`; `verified.json` shows the jobhunt role between Atelier Dacko and AI Agency with all 3 bullets intact. Tailor-side spot-check (verification bullet 2) deferred to next `apply --url`.

---

### Phase 3 — Escalate sub-50% audit coverage to `block` verdict

**Goal:** Stop the pipeline from silently shipping applications that the keyword screen will toss before a human sees them.

**Why:** Audit found you submitted manual:db425a17 at **0% coverage** (OCR/Tesseract/Airflow — none in your stack) and manual:4bcf846a at **43%** (ElectronJS/WebSockets — none in your stack). Both rendered `revise`, which still produces docs and a fill-plan, so it's frictionless to press `y`. At <50% coverage the resume is effectively invisible to the screen for the JD's actual must-haves; submitting it is noise, not signal. Escalating to `block` makes apply_cmd skip the job and log the reason (per AGENTS.md verdict semantics).

**Files to touch:**
- `src/jobhunt/pipeline/audit.py` — adjust the verdict ladder around line 288–297 so `coverage_pct < HARD_COVERAGE_FLOOR` (proposed 50) escalates to `block` instead of `revise`. New constant `HARD_COVERAGE_FLOOR` alongside `MIN_KEYWORD_COVERAGE_PCT`.
- `tests/pipeline/test_audit.py` (or equivalent existing test file) — new case asserting `coverage_pct=0` → `block`, `coverage_pct=43` → `block`, `coverage_pct=60` → `revise`, `coverage_pct=80` → `ship`.
- `AGENTS.md` "Post-generation audit rules" §4 — note the new hard floor under the verdict semantics.

**Functions to add/change:**
- `pipeline.audit.audit` — change: extend verdict ladder with the new hard-floor check.

**Reuse audit:**
- Search terms: `rg 'MIN_KEYWORD_COVERAGE_PCT|verdict\s*=\s*"' src/jobhunt/pipeline/audit.py`
- Candidates found: `MIN_KEYWORD_COVERAGE_PCT` already exists as the soft threshold (70%, the `revise` line). The existing constant + verdict ladder is the right place; adding a second threshold beside it reuses the pattern.
- Why not reused-as-is: the existing constant is the *soft* line. We need a new *hard* floor; a second named constant is clearer than a magic literal and matches the existing module's style.

**Verification:**
- New unit tests pass: 0% → `block`, 43% → `block`, 60% → `revise`, 80% → `ship`.
- Replay `apply --url` against the OCR JD (`manual:db425a17`'s URL) — verdict now `block`, no docs rendered, `apply_cmd` logs the skip.
- Replay against an 80%+ JD (e.g. the AI Developer JD that was 100%) — verdict still `ship`, docs render unchanged.

**Status:** [x] **done** 2026-05-27. Added `HARD_COVERAGE_FLOOR_PCT = 50` next to `MIN_KEYWORD_COVERAGE_PCT` in [audit.py:43](src/jobhunt/pipeline/audit.py#L43). Verdict ladder in [audit.py:288](src/jobhunt/pipeline/audit.py#L288) now: fabrication → block; coverage < 50 → block; cover_violations / alignment / coverage < 70 → revise; else ship. Reworked `test_audit_revise_on_low_coverage` → `test_audit_revise_on_borderline_coverage` (60% case), added `test_audit_block_on_below_hard_floor_coverage` (20%) and `test_audit_block_on_zero_coverage` (0%, mirrors the OCR replay). Full pytest run: 675 passed. AGENTS.md §"Post-generation audit rules" item 1 updated with the new floor + the date'd context.

---

### Phase 4 — Add framing-overreach patterns to `cover_validate`

**Goal:** Catch soft-fabrication claims like "live data streams" that don't match any verified bullet but slipped past the token-level watchlist.

**Why:** Audit found cover #4 (manual:4bcf846a) opened with *"two plus years of experience building full-stack applications in TypeScript, Node.js, and Express that handle live data streams and complex user workflows"* — Casey has zero verified live-stream / real-time / WebSocket work; that's Shopify/HubSpot CMS. The validator's `_FABRICATION_WATCHLIST` is token-based (Bun, Kubernetes, Pinecone, etc.) so framing phrases bypass it. Extend `_DEFENSIVE_PATTERNS` (or add a sibling tuple `_OVERREACH_PATTERNS`) with regex for capability claims absent from `verified.json` work_history bullets: *"live data streams"*, *"real-time \\w+"*, *"WebSocket(s)?"*, *"event-driven \\w+"*, *"streaming pipeline"*, etc. — gated on the term not appearing in any verified bullet, so legitimate work still passes.

**Files to touch:**
- `src/jobhunt/pipeline/cover_validate.py` — add `_OVERREACH_PATTERNS` and integrate into the `validate_cover` checks; keep gate logic small (membership test against a `verified_bullet_text` blob).
- `tests/pipeline/test_cover_validate.py` — cases: "live data streams" cover + no live-stream bullet → violation; "live data streams" cover + matching verified bullet → pass; unrelated cover → pass.

**Functions to add/change:**
- `pipeline.cover_validate._check_overreach` — add — runs the regex tuple against the cover, suppresses matches whose term appears anywhere in the verified bullet blob.
- `pipeline.cover_validate.validate_cover` — change — call `_check_overreach` alongside existing `_check_defensive_patterns` / `_check_fabrication_watchlist`.

**Reuse audit:**
- Search terms: `rg '_DEFENSIVE_PATTERNS|_FABRICATION_WATCHLIST|_check_' src/jobhunt/pipeline/cover_validate.py`
- Candidates found: `_DEFENSIVE_PATTERNS` (line 58) is the closest sibling — also a list of `(regex, label)` tuples. `_FABRICATION_WATCHLIST` (line 182) is token-based with `_NEGATION_PRECEDES_RE` suppression — same suppression pattern is reusable here (verified-bullet membership as the "negation").
- Why not reused-as-is: `_DEFENSIVE_PATTERNS` targets gap-volunteering ("rather than X", "exposure to"), a different failure mode. A separate constant keeps intent clear, but the helper structure mirrors `_check_fabrication_watchlist` directly — copy that shape, swap the corpus.

**Verification:**
- New unit tests pass per the cases above.
- Replay manual:4bcf846a cover through `validate_cover` — produces a violation citing "live data streams"; verdict becomes `revise` even at 43% coverage (already `block` after Phase 3, but the violation still appears in `audit.json` for traceability).
- Existing `cover_validate` tests still green.

**Status:** [x] **done** 2026-05-27. Added `_OVERREACH_PATTERNS` constant in [cover_validate.py:100](src/jobhunt/pipeline/cover_validate.py#L100) (7 regex/label pairs: live data streams, real-time streaming/processing, websockets, event-driven architecture, streaming pipelines, distributed systems, high-throughput claim). Wired the check inline at the bottom of `validate_cover` (mirrors the fabrication-watchlist structure: word-boundary regex + `_verified_skill_blob` suppression + `_NEGATION_PRECEDES_RE` suppression). Surface text `unverified capability claim: '<label>'`; added `("unverified capability claim:", "unverified_capability")` to `_VIOLATION_PREFIXES` so `analyze validators` aggregates it. 6 new tests cover: live-data-streams fires, websockets fires, clean cover passes, negated cover doesn't fire, verified-blob suppression works, `categorize_violation` returns the stable rule_id. Full pytest: 681 passed. AGENTS.md §"Post-generation audit rules" item 2 updated with the new pattern + dated rationale.

---

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
