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

## Interview-Prep Expansion — "Job Interview Buddy" (2026-06-09)

Plan: `~/.claude/plans/what-are-some-ways-fuzzy-meadow.md`. Expand
`interview-prep` into the source-of-truth prep surface: flexible intake
(job-id / URL / pasted JD), its own configurable model slot, an accumulating
multi-round doc (agency -> company -> company_leader), deeper robots-safe
research, and tunable prep-only validators. Honesty/no-fabrication stays hard;
no LinkedIn/Indeed/Glassdoor scraping.

### Phase 1 — Flexible intake (walking skeleton)

**Goal:** `interview-prep` accepts a job-id, `--url`, or pasted JD and produces a prep doc end-to-end.

**Files touched:**
- src/jobhunt/commands/_manual_intake.py — NEW: `synth_manual_job` (build/fetch + robots-check + upsert), extracted from `apply_cmd._resolve_manual`.
- src/jobhunt/commands/apply_cmd.py — `_resolve_manual` now delegates synth+upsert to the shared helper; dropped now-unused manual imports.
- src/jobhunt/commands/interview_prep_cmd.py — `job_id` now optional; added `--url`, `--title`, `--company`, `--description-from-stdin`; new `_resolve_job_id` resolves intake to a DB job id.
- tests/test_manual_intake.py — NEW: paste happy-path (synth+upsert+commit) + `_resolve_job_id` validation branches.
- README.md — documented the new intake flags + paste-the-JD guardrail.

**Reuse audit:**
- Search terms: `grep -n "build_job_from_text\|fetch_url_as_job\|robots_allowed\|upsert_job" src/jobhunt/commands/`.
- Candidates found: `ingest.manual.{build_job_from_text,fetch_url_as_job,robots_allowed}`, `db.upsert_job`, and the synth+upsert body inside `apply_cmd._resolve_manual`.
- Why reused: extracted the duplicated synth+upsert into `_manual_intake.synth_manual_job` so apply and prep share one code path rather than duplicating it.

**Verification:**
- `uv run pytest tests/test_manual_intake.py tests/test_manual_ingest.py tests/test_apply_picks.py -q` — 24 passed.
- Full suite: 833 passed, 1 pre-existing unrelated failure (`test_parse_docx::test_parse_baseline_round_trip`, github-url prefix; fails on clean HEAD too).
- Manual E2E: piped a JD to `interview-prep --description-from-stdin --title ... --company ... --no-llm` -> synthesized `manual:51f4076dcb81`, upserted into jobs DB (verified via direct query), wrote the skeleton doc. Smoke row + doc cleaned up.

**Status:** [x] done

## Master Audit-Fix Plan (2026-06-09, approved 2026-06-10)

Per-feature improvement plan produced by a full-repo audit. Every defect
below was verified against source (or by direct function test), not assumed.
Phases are ordered for execution. Trivial-tier items are batched into one
hygiene phase per the Blast-radius rules. Decision gates were resolved at
approval on 2026-06-10: B3 deferred, C1 option (a) apply-time, F1 option (a)
slash command.

Sequencing note: the in-flight Interview-Prep Expansion (fuzzy-meadow plan,
Phase 1 done) continues on its own track. Nothing below touches
`interview_prep` files, so the two tracks can interleave. Recommended order:
finish a fix phase here, then an interview-prep phase, alternating.

## Feature: Test suite and doc hygiene

### Phase A1 — Fix the pre-existing parse_docx round-trip failure

**Goal:** Make `test_parse_docx::test_parse_baseline_round_trip` pass again.

**Files to touch:**
- src/jobhunt/resume/parse_docx.py — reconcile the project-url prefix handling (test expects `github.com/SimBuds/Auto-Agent`, parser currently emits a prefixed form)
- tests/test_parse_docx.py — only if the test's expectation, not the parser, is what changed intentionally (decide at execution from git blame)

**Functions to add/change:**
- parse_docx project-url normalization — change — strip or keep the `https://` prefix consistently with the rest of `VerifiedFacts`

**Reuse audit:**
- Search terms: `grep -n "github" tests/test_parse_docx.py src/jobhunt/resume/parse_docx.py`
- Candidates found: the existing http-to-https normalization block in parse_docx (line ~249)
- Why not reused as-is: it is the suspect code path, the fix lands inside it

**Verification:**
- `uv run pytest tests/test_parse_docx.py -q` green (fails on clean HEAD today)
- `uv run pytest -q` shows zero failures repo-wide

**Outcome (2026-06-10):** git blame showed the test expectation (bare
`github.com/...`, PB3 commit 5d2d153) reflects the docx's visible text, while
commit f36ac4c's hyperlink-target substitution (intentional, shared with the
contact line) started emitting scheme-prefixed targets. Fixed at the Project
parse site only: strip a leading `https://` or `http://` from the project url
so the visible bare-domain form is restored. Test file untouched. Verified:
`tests/test_parse_docx.py` 18 passed, full suite 834 passed (was 833 + 1
fail), ruff clean. Pre-existing mypy `no-untyped-def` on
`_paragraph_text_with_links` (line 231) is outside this phase's surface.

**Status:** [x] done

### Phase A2 — Batch trivial hygiene fixes (stale comments + dead knob)

**Goal:** Remove the dead `score_concurrency` config knob and correct the three stale context-length comments.

**Files to touch:**
- src/jobhunt/config.py — delete `PipelineConfig.score_concurrency` (defined, never read anywhere in src) and fix the `num_ctx=16384` comment in `GatewayConfig` to 32768
- src/jobhunt/pipeline/score.py — fix the `MAX_DESC_CHARS` header comment (claims `OLLAMA_CONTEXT_LENGTH` currently 16384, but context is app-owned at 32768 per AGENTS.md)

**Functions to add/change:** none (comments + one field deletion).

**Reuse audit:**
- Search terms: `grep -rn "score_concurrency" src/ tests/` and `grep -rn "16384" src/`
- Candidates found: `score_concurrency` has zero call sites, the 16384 strings appear only in comments
- Why not reused: deletion/correction, nothing new introduced

**Verification:**
- `grep -rn "score_concurrency\|OLLAMA_CONTEXT_LENGTH.*16384" src/` returns nothing
- `uv run pytest tests/test_config.py -q` green

**Outcome (2026-06-10):** deleted `PipelineConfig.score_concurrency`, fixed
the `num_ctx=16384` comment in `GatewayConfig.tasks` to 32768, and rewrote the
`MAX_DESC_CHARS` header comment in `pipeline/score.py` to point at the
app-owned `num_ctx=32768` in `gateway.client._DEFAULT_OPTIONS` instead of the
unset `OLLAMA_CONTEXT_LENGTH`. The gateway client comment was already correct
and untouched. Greps return nothing, full suite 834 passed. The live
`~/.config/jobhunt/config.toml` still contains `score_concurrency = 2`, which
Pydantic ignores (default extra policy). Verified the live config loads.
Casey can hand-delete that line at leisure. Pre-existing ruff SIM110 in
`score.py` fires on clean HEAD too, out of scope. README's example config
block also listed the knob, removed per the Definition of Done doc check
(README was not in the planned file list, surfacing the expansion here).

**Status:** [x] done

## Feature: Ingest (Toronto coverage correctness)

### Phase B1 — Stop GTA-homonym cities abroad from passing the location filter

**Goal:** Reject locations like `Cambridge, MA`, `Richmond Hill, NY`, `Hamilton, New Zealand`, and `Milton Keynes, UK` that currently pass `is_gta_eligible` via substring city match.

**Files to touch:**
- src/jobhunt/ingest/_filter.py — add a non-Canada anchor veto to the city branch of `is_gta_eligible`: extend the existing `_NON_CANADA_REMOTE` idea into a `_NON_CANADA_ANCHOR` regex covering country names (United States, UK, United Kingdom, New Zealand, Australia, Belgium, Ireland, etc.) plus comma-delimited US state codes (`, MA`, `, NY`, `, VT`, `, IL`, ...) excluding `ON`. A GTA city name only accepts when no non-Canada anchor is present in the same string. Word-boundary the city names while in there so `Milton` stops matching inside `Milton Keynes` (note `milton` inside `hamilton` is currently harmless only because both are allowlisted).
- tests/test_gta_filter.py — add cases for the seven verified false accepts above plus regression cases (`Toronto, ON`, `Cambridge, ON`, `Hamilton, Ontario`, `Remote - Canada` must still pass)

**Functions to add/change:**
- _filter.is_gta_eligible — change — anchor-veto on the city branch
- _filter._NON_CANADA_ANCHOR — add — module-level compiled regex

**Reuse audit:**
- Search terms: `grep -rn "NON_CANADA\|CANADA_STRONG\|state\|country" src/jobhunt/ingest/_filter.py`
- Candidates found: `_NON_CANADA_REMOTE` (remote-branch veto only)
- Why not reused as-is: it lacks state codes and several country names, and is applied only on the remote branch. Extending its pattern shape into a sibling regex reuses the mechanism without overloading remote-branch semantics.

**Verification:**
- New tests fail before, pass after: `uv run pytest tests/test_gta_filter.py -q`
- One-liner repl: `is_gta_eligible("Cambridge, MA")` is False, `is_gta_eligible("Cambridge, ON")` is True
- Live spot check on next scan: Workday global tenants stop contributing non-Canadian rows

**Outcome (2026-06-10):** added `_GTA_CITY_RE` (word-boundaried allowlist
match) and `_NON_CANADA_ANCHOR` (country/region names + comma-delimited US
state codes, plus `milton keynes` as an explicit homonym) to `_filter.py`. The
city branch now accepts only when no anchor is present. Two judgment calls
logged: `CA` is excluded from the state-code tier because aggregators emit
"Toronto, CA" as a country code (covered by a regression test), and a
multi-location string like "Toronto, ON / New York, NY" would be vetoed by
the `, NY` anchor — accepted as a rare false negative rather than adding a
Canada-override tier. The 7 verified false accepts fail on HEAD and pass
after (verified via stash round-trip); 38/38 filter tests green, full suite
845 passed, mypy --strict clean. SIM103 in `_filter.py` pre-exists on HEAD,
out of scope. Live Workday spot check happens on Casey's next scan.

**Status:** [x] done

### Phase B2 — Make direct-vs-aggregator dedupe real within a scan

**Goal:** Dedupe the same posting arriving from a direct ATS source and an aggregator (Greenhouse + Adzuna) in one scan, which the current `_dedup_key` docstring claims but the implementation cannot do.

**Files to touch:**
- src/jobhunt/commands/scan_cmd.py — change `_dedup_key` to return a set of keys: direct sources contribute both their `job.id` and the normalized `title:company` shadow key, aggregators contribute `title:company` only. The drain loop drops a job when any of its keys was seen. Direct sources win ties regardless of arrival order by a second pass rule: when an aggregator row's shadow key was already claimed, drop the aggregator row, and when a direct row's shadow key was claimed by an aggregator row already upserted this scan, keep the direct row (richer JD) and let `upsert_job`'s description backfill handle the rest.
- tests/ — new test module or extend an existing scan-side test with a fake drain over synthetic Jobs

**Functions to add/change:**
- scan_cmd._dedup_key — change — return `tuple[str, ...]` of keys
- scan_cmd._ingest_all drain loop — change — multi-key seen-set handling

**Reuse audit:**
- Search terms: `grep -rn "dedup" src/ migrations/`
- Candidates found: `_dedup_key` itself, Workday's per-adapter `dedup_key` (path-based, adapter-internal)
- Why not reused as-is: the Workday key is adapter-internal and not cross-source. The fix is a behavior change to the existing chokepoint, not a new mechanism.

**Verification:**
- New test: a Greenhouse Job and an Adzuna Job with the same normalized title+company yield one upsert in either arrival order
- `uv run pytest tests/test_db_writes.py -q` and the new test green

**Outcome (2026-06-10):** `_dedup_key` now returns a key tuple: direct ATS
sources yield `(job.id, title:company shadow)`, aggregators yield `(shadow,)`.
A new pure helper `_dedup_decision` (testability extraction in the same file,
surfacing the one addition beyond the planned function list) encodes the
drain rule: a row drops only when its identity key (keys[0]) was claimed, so
an aggregator drops behind any earlier copy while a direct row is never
blocked by a shadow. For the aggregator-first order, the drain deletes the
aggregator's jobs row before upserting the direct one. That deletion is
restricted to rows INSERTED THIS SCAN (tracked in `agg_shadow`, populated
only on a True `upsert_job` return): in-scan rows are unscored since scoring
runs after ingest, while a pre-existing aggregator row may carry scores or
applications, so the cross-scan pair is left alone (that is B3, deferred). A
regression test pins the pre-existing-row case. `inserted` is decremented on
supersede so the scan summary stays truthful. Existing
`test_dedup_key_greenhouse_uses_job_id` updated for the tuple shape. New
module `tests/test_scan_dedupe.py` covers both arrival orders, aggregator
copies collapsing, distinct postings kept, and key shapes. Verified: 47
passed across the three touched test modules, full suite 851 passed, ruff
clean. Mypy `arg-type` on `_refresh_source_row` pre-exists on HEAD (line 585
verbatim), out of scope.

**Status:** [x] done

### Phase B3 (decision gate, risky tier) — Persistent cross-scan dedupe

**Goal:** Stop an aggregator row scored in scan N from coexisting with the direct-ATS row of the same posting ingested in scan N+1.

**Decision needed before planning details.** Options:
- (a) Schema migration adding a `jobs.dedup_key` column plus a pre-upsert lookup. Clean, but a schema change is risky tier and re-keys history.
- (b) Query-time suppression only: `list` and `apply --top` exclude an aggregator row when a direct-source row shares its normalized title+company. No schema change, duplicates still exist and still get scored.
- (c) Defer. B2 alone removes most duplicate scoring because both sources usually appear in the same scan window.

Decision (2026-06-10): (c) defer. Revisit if `list` shows real duplicate pairs after B2 has run for two weeks.

**Status:** [ ] deferred (revisit ~2026-06-24 after two weeks of post-B2 scans)

## Feature: Apply (Adzuna URL quality and tailoring strength)

### Phase C1 (decision gate) — Wire or remove the orphaned `resolve_redirect`

**Goal:** Reconcile code with PLAN.md: `http.resolve_redirect` is implemented and fully tested but has zero call sites, while PLAN.md claims Adzuna redirects resolve at ingest.

**Decision needed.** Options:
- (a) Resolve at apply time (recommended): in `apply_cmd`, when the picked job's source is `adzuna_ca`, chase the redirect once and persist the employer URL onto the job row before tailoring, so the fill-plan and `add` suggestion see the real ATS URL. Cost: one HEAD chase per application, not per ingested row.
- (b) Resolve at ingest as PLAN.md states. Cost: one chase per Adzuna row per scan (hundreds of requests), not worth it.
- (c) Delete `resolve_redirect` plus its test module and fix PLAN.md. Zero behavior gain.

Decision (2026-06-10): (a) resolve at apply time, then fix the PLAN.md sentence to say apply-time.

**Files to touch (under option a):**
- src/jobhunt/commands/apply_cmd.py — call `resolve_redirect` in `_apply_io_phase` (or `_run_lifecycle` pre-step) for adzuna-sourced rows, persist via a small `db` update
- src/jobhunt/db.py — add `update_job_url` helper (parameterized SQL, no ORM)
- PLAN.md — correct the redirect-resolution sentence
- tests/ — unit test the apply-side hook with a mock transport (reuse the MockTransport pattern from tests/test_redirect_resolve.py)

**Reuse audit:**
- Search terms: `grep -rn "resolve_redirect" src/ scripts/ tests/`
- Candidates found: `http.resolve_redirect` (orphaned), `tests/test_redirect_resolve.py` (MockTransport pattern)
- Why reused: the function IS the reuse, the phase only adds the call site

**Verification:**
- New test: adzuna-sourced row gets its URL replaced by the chased terminal URL before render
- `uv run pytest tests/test_redirect_resolve.py -q` plus the new test green

**Outcome (2026-06-10):** option (a) shipped. New `db.update_job_url`
(parameterized UPDATE, mypy-strict clean). New `apply_cmd._resolve_adzuna_url`
called at the top of `_apply_io_phase`: adzuna_ca rows with a URL get one
redirect chase via the existing `with_client` + `resolve_redirect` +
`RateLimiter(1.0)` (sends `cfg.ingest.user_agent`), the terminal URL is
persisted onto the job row and the in-memory Job is model_copied, so the
fill-plan, recorded application, and `add` suggestion all see the employer
URL. Non-adzuna rows, URL-less rows, and unresolved chases are no-ops
(`resolve_redirect` never raises by design). PLAN.md's ingest-time claim
corrected to apply-time. New `tests/test_apply_redirect.py` (4 tests, chase
stubbed via monkeypatch since the chase itself already has a MockTransport
suite): resolved+persisted, non-adzuna untouched, missing URL skipped,
unresolved unchanged. Verified: 10 passed across both redirect modules, full
suite 855 passed. Ruff and mypy on apply_cmd match the HEAD baseline exactly
(9 ruff / 14 mypy pre-existing findings, none introduced).

**Status:** [x] done

### Phase C2 — Deep-fetch thin Adzuna JDs at apply time before tailoring

**Goal:** When applying to an Adzuna row whose description is under `cfg.pipeline.thin_jd_chars`, fetch the resolved employer page (robots-checked) and tailor against the full JD instead of the 500-char snippet.

This is the highest-leverage tailoring-strength fix: the tailor, audit
keyword coverage, and cover anchors all degrade on snippet-length JDs, and
the thin-JD score cap already marks these rows as low-confidence.

**Files to touch:**
- src/jobhunt/commands/apply_cmd.py — pre-tailor step: if source is adzuna_ca and description is thin, call the existing `ingest.manual.fetch_url_as_job` against the (C1-resolved) employer URL, backfill the description via the `upsert_job` backfill path or a direct UPDATE, then delete the job's score row so the next scan re-scores against the full JD (prompt_hash is unchanged, so deletion is the correct invalidation). Apply continues with the enriched description this run.
- README.md — document the deepen behavior and the robots/`--force-robots` interaction
- tests/ — unit test the thin-detection branch with a stubbed fetch

**Functions to add/change:**
- apply_cmd._deepen_thin_adzuna — add — the pre-tailor enrichment step
- db helper for score-row invalidation — add only if `db.py` lacks one (check `grep -n "DELETE FROM scores" src/` at execution)

**Reuse audit:**
- Search terms: `grep -rn "fetch_url_as_job\|robots_allowed\|thin_jd" src/`
- Candidates found: `ingest.manual.fetch_url_as_job` (fetch + robots + Job synth), `upsert_job` description backfill, `cfg.pipeline.thin_jd_chars`
- Why reused: all three exist, the phase composes them with no new utility

**Verification:**
- New test: thin adzuna row triggers the fetch stub, fat row does not, non-adzuna row does not
- Manual E2E on one live thin Adzuna job: audit coverage computed against the full JD, not the snippet

**Outcome (2026-06-10):** new `apply_cmd._deepen_thin_adzuna`, called at the
top of `_apply_llm_phase` (the plan said pre-tailor, and the tailor consumes
the description in the LLM phase, so that is the correct hook, not
`_apply_io_phase`). Flow for adzuna_ca rows under `thin_jd_chars`: resolve
the redirect via C1's `_resolve_adzuna_url`, robots-check the employer URL
(no `--force-robots` on this path — that flag stays `apply --url`-only),
fetch via the existing `ingest.manual.fetch_url_as_job`, and when the result
is longer persist it with a direct UPDATE plus `DELETE FROM scores` for the
row (prompt_hash unchanged, deletion is the invalidation), continuing this
apply with the enriched Job. Best-effort by design: robots denial, fetch
failure, or a shorter result keeps the snippet and never touches the score
row. One known double-chase: `_apply_io_phase`'s C1 hook re-resolves the
original in-memory URL after the LLM phase (one redundant idempotent HEAD
per thin-adzuna application) — accepted over threading the enriched Job
through `_LLMPhaseResult`. Six tests in `tests/test_apply_deepen.py` (all
seams stubbed): enrich+persist+invalidate, fat row no-op, non-adzuna no-op,
robots denial, fetch failure (score untouched), shorter-result no-op.
Verified: full suite 861 passed; ruff (11) and mypy (14) match the HEAD
baseline exactly (my new code is clean — a copied lowercase-`callable` idiom
was corrected to `Callable` before landing). README documents the deepen
behavior and the robots interaction under `apply`. Manual live E2E on one
thin Adzuna job is Casey's to run at next apply (audit coverage should
compute against the full JD).

**Status:** [x] done

## Feature: Pipeline quality harness

### Phase D1 — Golden-JD tailor eval harness (manual, live Ollama)

**Goal:** Add `scripts/eval_tailor.py`, a manual live-Ollama harness that runs the tailor+audit pipeline over a fixed set of in-lane golden JDs and reports coverage, retry counts, and validator hits, so prompt or model swaps are measurable instead of vibes.

Per the Testing section of AGENTS.md this stays out of CI (live Ollama is
manual). It mirrors the existing `scripts/bench_models.py` pattern.

**Files to touch:**
- scripts/eval_tailor.py — new script: loads golden JDs, runs score → tailor → audit per JD, prints a table (coverage pct, verdict, fabrication retries, validator rule_ids)
- tests/fixtures/golden/ — four to six JD text files covering the lane: shopify developer, hubspot/CMS developer, solutions engineer, wordpress/e-commerce, one AI-adjacent, one deliberate off-lane control
- README.md — one paragraph under a Maintenance or Scripts heading

**Functions to add/change:**
- eval_tailor.main — add — async runner over the golden set

**Reuse audit:**
- Search terms: `grep -rn "golden\|eval" scripts/ tests/` and `ls scripts/`
- Candidates found: `scripts/bench_models.py` (live-Ollama harness shape), `pipeline.score/tailor/audit` entry points
- Why not reused as-is: bench_models benchmarks models, not pipeline quality per JD. Its CLI/reporting shape is reused, the loop body is the existing pipeline functions.

**Verification:**
- `uv run python scripts/eval_tailor.py` produces the report against live Ollama (manual)
- `uv run pytest -q` unaffected (script imports only, no test-time network)

**Status:** [ ] not started

## Feature: Documentation reconciliation

### Phase E1 — Reconcile PLAN.md with shipped behavior

**Goal:** Fix the PLAN.md statements the audit found contradicting the code.

**Files to touch:**
- PLAN.md — (1) Adzuna redirect-resolution sentence per the C1 decision, (2) Job Bank line still says "RSS — federal government feed" while AGENTS.md documents the RSS-is-dead HTML-scrape carve-out, (3) spot-check the Models and Database sections against migrations 0004-0008 and current gateway defaults

**Functions to add/change:** none (docs only).

**Reuse audit:** not applicable (no code).

**Verification:**
- Re-read diff against AGENTS.md statements, no remaining contradictions on redirects, Job Bank, or context ownership

**Status:** [ ] not started (run after C1)

## Feature: Claude Code integration (decision gate, no runtime change)

### Phase F1 (decision gate) — Claude-assisted artifact review without breaking local-only runtime

**Goal:** Get frontier-model quality onto the highest-stakes artifacts without violating the AGENTS.md rule "Do not add cloud LLM provider code to the runtime path."

**Decision needed.** Options:
- (a) Recommended: add a `.claude/commands/review-application.md` slash command for Claude Code that reads `data/applications/<id>/` artifacts (tailored resume markdown, cover, audit.json, the JD from the DB) and produces a critique plus suggested edits Casey applies by hand. Tooling-side only, zero runtime code, honesty checks remain the deterministic gate.
- (b) Amend AGENTS.md to allow an opt-in cloud tailor slot behind an explicit flag. This is a contract change and needs its own discussion, not a silent relaxation.
- (c) Defer until D1's eval harness shows where the local model actually underperforms.

Decision (2026-06-10): (a) now. Revisit (b) only if D1 evidence shows local tailoring is the bottleneck rather than JD thinness (C2 may close most of the gap on its own).

**Status:** [ ] not started (option a approved 2026-06-10)

## Execution order

1. A1 (suite green) then A2 (hygiene)
2. B1 (GTA filter, immediate scan-quality win)
3. B2 (in-scan dedupe)
4. C1 (apply-time redirect), then C2 (tailoring strength)
5. D1 (eval harness)
6. E1 (docs reconcile)
7. F1 (review slash command)
8. B3 revisit after two weeks of post-B2 scans

Interview-Prep Expansion phases interleave between any of the above. Each
phase above ends with its handoff line per the Workflow Contract.
