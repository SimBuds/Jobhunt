# Jobhunt design notes

A CLI tool that ingests Toronto-area jobs from public ATS APIs, scores them
against Casey's parsed baseline resume using local Ollama models, tailors
resumes and cover letters per role, and assists with form autofill in a
headed browser. Casey clicks Submit.

This document explains the *why*. `AGENTS.md` is the *how* (conventions,
guardrails, project structure). `README.md` is for end-users.

---

## Goals

1. **Replace cloud per-job AI spend with local inference.** Scoring,
   tailoring, and cover letters all run on Ollama. Token cost at runtime is
   zero.
2. **Future-proof the knowledge layer.** Profile facts live in
   `kb/profile/verified.json` (regenerated from the baseline resume).
   Prompts live in `kb/prompts/*.md` with JSON-schema frontmatter. Model swaps
   don't break them.
3. **Stay ToS-defensible.** Public ATS APIs only. No LinkedIn / Indeed /
   Glassdoor scraping. No bot-submitted applications: Playwright fills the
   form, human clicks Submit.
4. **Honesty by construction.** Tailoring is constrained to facts in
   `verified.json`. Roles must match `(employer, dates)` exactly. "Familiar"
   skills can't be promoted into Core categories. The score prompt
   auto-declines roles whose required years exceed the candidate's by more
   than three (absent a transferable bridge) and people-management titles
   (Manager / Director / Head of / VP). Senior-band titles are scored, not
   declined (July 2026): IC-coding-heavy senior JDs land in the 55–70 band
   under 4 YoE, 60–85 at 4+. Honesty applies to the *artifacts* — the resume
   and cover can never claim beyond verified facts — while scoring
   visibility is deliberately wider than it was.

## Design principles

**Local-first at runtime.** Every per-job AI call routes through
`jobhunt.gateway` to Ollama at `http://localhost:11434`. No cloud calls in
the hot path.

**Constrained output.** Every structured LLM call uses Ollama's `format`
parameter with a JSON schema from the prompt's frontmatter.

**Single hot model.** All four task slots (score, tailor, cover, answer) run on one
model, bare **`qwen3.5:9b`** by default (since 2026-05-28). The gateway pins
its own options on every call (`num_ctx=32768` + samplers, see
`gateway.client._DEFAULT_OPTIONS`) and always sends its own system message (the
task prompt), so structured-task behavior is fully defined in-repo and no custom
Modelfile is needed. No
reload churn between tasks. Quality is held by deterministic post-processing
(score clamp, cover validator + retry, audit) together with the model's tool-use + reasoning
capability. The cascade-by-difficulty design (8B for scoring, 14B for
generation) was abandoned in May 2026 once the guardrail layers made
model-size differential less load-bearing than the 5-15 s reload cost
between every call. Set in config (`gateway.tasks`).

**Knowledge base is markdown + JSON.** No model-specific syntax baked in.

## Hardware budget

| Resource | Allocation |
|---|---|
| GPU VRAM (10 GB total, all available to Ollama) | Arch idles around 1.5 GB on the GPU, so `OLLAMA_GPU_OVERHEAD` is intentionally unset. On the new Ollama engine (0.30.3) bare `qwen3.5:9b` Q4_K_M lands around ~5.6 GB resident at `num_ctx=32768`, 100% GPU, with a `q4_0` quantized KV cache (`OLLAMA_KV_CACHE_TYPE=q4_0` + `OLLAMA_FLASH_ATTENTION=1`, switched from q8_0 on 2026-07-27 to preserve VRAM headroom). **Context length is app-owned**: the gateway pins `num_ctx=32768` in `_DEFAULT_OPTIONS` on every call (without it Ollama's 4096 default truncates the ~6k-token prompts and the model emits prose instead of schema JSON), and `OLLAMA_CONTEXT_LENGTH` is deliberately unset so each project sharing the box picks its own window. The q8_0 weight build was rejected: it spills to CPU at 16k and 32k here. Single hot model pinned during active work via per-call `keep_alive=-1`, which takes precedence over the systemd `OLLAMA_KEEP_ALIVE=30m` idle fallback, plus a warm-up call at scan start. Reasoning (`think`) is disabled at the gateway so structured calls don't blow past the timeout. |
| System RAM (32 GB) | Embeddings on CPU, SQLite cache, and Playwright when active. |
| Disk | Models in `~/.ollama/models`, project DB in `data/jobhunt.db`. |

## Models (default)

| Task | Model | Why |
|---|---|---|
| Fit-score / tailor / cover / answer | bare `qwen3.5:9b` (default since 2026-05-28) | Single hot model, no reload churn. Strong open tool-use model. Reasoning is disabled (`think: false`) at the gateway since structured-output latency under thinking blew past the timeout. The gateway pins app-owned options (`num_ctx=32768` + samplers) and sends its own system message, so behavior is defined in-repo with no custom Modelfile. Post-processing guardrails (score clamp, cover validator + retry, audit) carry quality alongside it. |
| Embeddings | `nomic-embed-text` | CPU. Reserved for future kb retrieval. |

All overridable in `~/.config/jobhunt/config.toml`. Per-call override via
`JOBHUNT_GATEWAY__TASKS__<SLOT>=<model>` env var.

## Sources (Toronto-focused)

- **Greenhouse** boards-api: most common in GTA tech listings.
- **Lever** `api.lever.co/v0/postings/<slug>`: common at GTA startups.
- **Ashby** posting API: growing share in 2025-26.
- **SmartRecruiters** public Posting API: no key needed, growing share at
  GTA mid-market employers.
- **Workable** public widget API
  (`apply.workable.com/api/v1/widget/accounts/{slug}`): no key needed.
- **Recruitee** public offers API (`{slug}.recruitee.com/api/offers/`): no
  key needed.
- **Workday CXS** per-tenant search: reaches the Big Five banks (RBC, TD,
  BMO, CIBC, Scotia), telcos (Telus, Bell, Rogers), Manulife, Sun Life,
  Loblaw Digital, Thomson Reuters. Tenants configured explicitly in
  `config.toml` as `tenant:host:site`.
- **Job Bank Canada**: HTML search-results scraper (sanctioned carve-out,
  2026-06). The public RSS is dead, so the adapter parses the search page.
  Job Bank is a Government of Canada public job-search service, its
  robots.txt has no Disallow (only `Crawl-delay: 5`, honored via a dedicated
  5 s limiter), and this carve-out does not generalize to any other site.
  See AGENTS.md ingestion rule 1 for the full rationale.
- **Generic employer career RSS / Atom**: opt-in per employer in
  `config.toml`.
- **Adzuna CA**: `country=ca&where=Toronto&distance=100`. Aggregates broadly
  and needs a free API key. `redirect_url` is stored as-is at ingest and resolved
  at apply time (one chase per application, not per ingested row) to the
  employer's actual posting page so autofill lands on the form, not Adzuna's
  listing redirect.

Filter pipeline: each adapter checks `is_gta_eligible(location)` before
yielding a job. The allowlist covers Toronto plus 21 surrounding
municipalities, including the Kitchener-Waterloo corridor (Waterloo,
Kitchener, Cambridge, Guelph) and Barrie, all within the README's 100 km
radius, plus Remote-Canada /
Remote-Ontario / Remote-EST. Bare "Remote" is rejected as ambiguous. The
`ON` province abbreviation only counts as a Canada hint when comma-delimited
(`Remote, ON`), never as the English word in `Remote (on-call) — US`.

Explicitly excluded (won't change without removing the no-scraping guardrail
in `AGENTS.md`):

- LinkedIn, Indeed, Glassdoor, ZipRecruiter: ToS, brittle, litigated.
- USAJobs and worldwide job APIs: out of GTA scope.

Applications Casey submits ON those platforms are still first-class data:
`jobhunt track` (July 2026) logs them without any scraping — paste-based
intake (`--paste` parses a copied LinkedIn job page; `--jd-from-stdin` for a
raw JD; `--no-jd` stubs an expired posting), channel attribution on the
`applications` row, and lifecycle updates (response / interview / outcome)
by company-name fragment. `analyze funnel` and `analyze response-rate
--by channel` then answer the question the whole tool exists for: which
channel actually converts to interviews.

## What this project deliberately doesn't do

- Auto-submission of applications (ToS risk, human stays in the loop).
- Web UI / mobile (CLI-first, no current need).
- Recruiter outreach automation (different problem, do not bolt on).

## Database

SQLite, plain SQL, no ORM. Schema in `migrations/`:

- `0001_init.sql`: companies, jobs, scores, applications, indexes.
- `0002_apply_tracking.sql`: adds `jobs.decline_reason` and
  `applications.applied_week` (ISO week label, e.g. "2026-W18") for cheap
  weekly rollups.
- `0003_outcomes.sql`: adds outcome-tracking columns to `applications` for
  the `config calibrate` interview-rate-by-score-band rollup.
- `0004_slug_probes.sql`: adds the `slug_probes` cache table (90-day TTL on
  misses) backing `discover slugs` and scan auto-discovery.
- `0005_response_tracking.sql`: adds `response_received_at`, `interview_at`,
  `outcome`, and recruiter columns to `applications` for the
  `analyze response-rate` and `list --no-reply` surfaces.
- `0006_recruiter_type.sql`: renames `recruiter_handle` to `recruiter_type`
  (enum-style: internal_recruiter, hiring_manager, external_agency, unknown).
- `0007_decline_category.sql`: adds `jobs.decline_category` plus its index
  for cheap decline-pattern rollups.
- `0008_answer_index.sql`: adds the `answers` table indexing saved
  `jobhunt answer` artifacts for `--recall` lookup.
- `0009_application_channel.sql`: adds `applications.channel` ('pipeline'
  default; linkedin / indeed / referral / recruiter / company-site / other)
  so manual applications logged via `jobhunt track` split out in
  `analyze funnel` and `analyze response-rate --by channel`. Channel is an
  application property, not a job property — a scanned Greenhouse job can
  still be applied to via LinkedIn Easy Apply.

## Honesty enforcement (the structural part)

The "no fabrication" rule from `kb/policies/tailoring-rules.md` is enforced
in six places, not just the prompt:

1. **Verified snapshot.** `convert-resume` emits `kb/profile/verified.json`.
   The tailoring prompt is constrained to only use facts from this file.
   All skill buckets (`skills_core`, `skills_cms`, `skills_data_devops`, `skills_ai`,
   `skills_projects`, `skills_familiar`) are **atomic lists** (one item per skill).
   `skills_projects` (Core-grade, demonstrated in Casey's shipped personal
   projects) is treated like a Core bucket by the score and tailor honesty
   checks, distinct from the academic/light-use `skills_familiar`. It is
   derived from the parsed `projects[].stack` lines, unioned with an explicit
   "Project Stack:" row in TECHNICAL SKILLS when the resume carries one, and
   de-duplicated case-insensitively with the first spelling winning. Deriving
   it matters because the bucket was previously fed by that row alone, so a
   resume keeping its stack on the project's own `Stack:` line parsed the line
   into `projects[].stack` and then discarded it: the bucket stayed empty and
   the tailor could claim none of the stack, since every item was absent from
   `verified.json` and the fabrication guard read it as invented. `verified.json`
   also carries a `projects[]` narrative (each with `name`, `url`, `stack`,
   `bullets`) parsed from the resume's PROJECTS section. It renders as a PROJECTS
   section on the tailored resume, is credited by audit keyword coverage, and may
   anchor a cover letter's centerpiece paragraph. `skills_ai` in
   particular must stay atomic, because the ATS keyword matchers and the audit's
   keyword-coverage check tokenize against individual items. `parse_docx._split_skills`
   is paren-aware (it splits on commas but treats commas inside parentheses as
   literal), so a comma-separated AI line such as "Ollama (Local LLM hosting),
   GPU optimization (cache, flash attention), ..." parses into atomic items with
   no hand-editing. `tests/test_parse_docx.py::test_parse_baseline_positioning_and_atomic_skills`
   locks this behavior.

   **Bucket-regression guard (July 2026).** `convert-resume` refuses to write
   `kb/profile/` when a bucket that carried items in the previous
   `verified.json` parses to empty. `parse_baseline` can only warn about rows it
   saw and could not place, so a row that is simply gone from the document
   produced no warning and the older partial-profile guard never fired. That
   blind spot is what let the 2026-07-25 reformat empty `skills_familiar`
   silently, which then broke `apply` on every job (the tailor rejected every
   Familiar skill as unverified) and quietly disabled the Familiar-only cap in
   check (4). `commands.convert_resume_cmd._bucket_regressions` compares the
   fresh parse against the prior snapshot and emits a loss warning per emptied
   bucket, so the existing fail-closed refusal, its `--force` hatch, and its
   exit code all apply unchanged. No prior snapshot, or an unreadable one, never
   blocks: a first run has nothing to regress against, and a corrupt snapshot is
   not evidence the resume lost anything.
2. **Schema-constrained output.** `kb/prompts/tailor.md` declares a JSON
   schema. Ollama's `format=<schema>` enforces shape at decode time.
3. **Post-decode invariants.** `pipeline.tailor._enforce_no_fabrication`:
   - rejects any role whose `(employer, dates)` is missing from
     `verified.json`.
   - rejects skill items not present in `verified.json` (substring tolerance
     for parenthetical variants like `Shopify (Liquid)` vs
     `Shopify (Liquid, Custom Themes)`).
   - rejects "Familiar" skills appearing in any non-Familiar category.

   **Retry layer (May 2026).** `_enforce_no_fabrication` raises a
   `FabricationError` carrying structured violations.
   `tailor_resume_with_retry` consumes those violations to build a
   kind-specific correction hint and re-runs the tailor LLM up to
   `cfg.pipeline.tailor_retry_attempts` times. The retry NEVER weakens
   the check: every attempt's output runs through the same invariants,
   and a final-attempt failure still raises so the apply loop skips the
   job. Recovery, not relaxation. It mirrors the cover-validator retry
   pattern in `pipeline.cover.write_cover_with_retry`.

   **Known gap: the retry loop is silent.** `apply` prints one
   "tailoring resume" line, then nothing until the loop resolves. Three
   failing attempts at 30 to 60 seconds each is up to three minutes of no
   output followed by a single error line, which reads as a hang rather than
   as work in progress. This is what made the July 2026 empty-`skills_familiar`
   failure look like a freeze. Per-attempt progress output in `apply_cmd` is
   the fix and is not yet implemented.
4. **Deterministic score (rewritten July 2026).** The LLM does not pick the
   number. It extracts the posting's requirements into two tiers
   (`must_haves` = hard requirements, `nice_to_haves` = wish list),
   annotating transferable bridges, and `pipeline.score` computes:

       base + tier1_weight * tier1_coverage
            + tier2_weight * tier2_coverage
            + ai_bonus

   Coverage is graded, not counted: an exact profile hit contributes 1.0 and
   a peer-family or annotated-bridge hit contributes
   `SCORE_TRANSFERABLE_CREDIT`, so an exact-stack fit outranks a bridged one
   instead of tying with it. Every extracted phrase is re-verified against
   `verified.json`, so a hallucinated match becomes a gap and *lowers* the
   score. An empty tier-2 folds its weight into tier-1, so a posting cannot
   score higher merely for omitting a wish list.

   **Why the LLM stopped scoring.** Across 169 live scores, six integers
   accounted for 136 of them and nothing ever exceeded 82. A 9B model at
   temperature 0 asked to choose from prose bands collapses onto the band
   midpoints, and the old rubric's "vary the score across jobs" instruction
   could never work, because each job is scored in its own call and the model
   never sees a batch to vary within.

   **Why tiers.** The previous clamp divided by one unweighted phrase list, so
   a posting whose core stack the candidate fully matched but whose
   nice-to-haves he missed computed as ~50 % coverage and was capped into the
   low 60s. That was the band the user observed on roles that produced real
   interviews. Weighting hard requirements far above wish-list items is what
   separates "missed a bonus" from "missed a requirement".

   A posting from which nothing at all could be extracted raises
   `PipelineError` (a model failure, skipped and retried next scan) unless it
   was declined, since a decline may legitimately stop the model reading.

   The five coefficients live in `[pipeline]` as `score_base`,
   `score_tier1_weight`, `score_tier2_weight`, `score_ai_bonus` and
   `score_transferable_credit`, and all five feed `prompt_hash`, so tuning any
   of them re-scores the backlog on the next `scan`. That is the point: scores
   computed under different weights are not comparable, and a queue sorted on
   two scales at once is worse than one that costs a re-scan to correct.

   **Score breakdowns (migration 0010).** Every score records how it was
   reached in `scores.breakdown`: per-tier matched/total/credit, the AI bonus,
   the pre-cap `computed` value, the post-cap `final` value, which ceilings
   bound (`caps_applied`), and the weights in force. The final integer alone is
   ambiguous. Measured on the live backlog, three separate postings all landed
   at exactly 70, but were computed 86, 90 and 90 at 92%, 100% and 100% tier-1
   coverage before the thin-JD ceiling flattened them. Tuning weights against
   the score column would therefore be tuning against the ceilings.
   `config calibrate` now reports interview rate by tier-1 coverage alongside
   the score bands, which is the signal the weights actually control. Rows
   written before the migration read back NULL and are excluded from that
   table with a count, never treated as zero coverage: a missing measurement is
   not a bad one.

   **Transferable crediting (July 2026):** the re-partition honors the same
   transferable rules the prompt promises, instead of demoting them: a
   phrase verifies via literal presence, a `PEER_FAMILIES` sibling
   (Vue→React), or the `(transferable: X)` annotation bridge — where X
   itself must verify against the profile, so bogus bridges fail closed.
   Cross-language bridges (Spring Boot↔Express, Java↔C#↔PHP↔TS) exist only
   in the prompt table + annotation path, deliberately not in
   `PEER_FAMILIES`, so audit keyword coverage and tailor surface-forms stay
   strict: the score can credit a bridge the resume is never allowed to
   claim.

   The May 2026 tiny-denominator carve-out retired with the coverage clamp it
   guarded. Signal-poor postings are handled by the thin-JD ceiling below,
   which is gated on description length rather than on how many phrases the
   model happened to extract.

   **Thin-JD ceiling (May 2026, corrected July 2026):** independently of the
   clamp, a description shorter than `pipeline.thin_jd_chars` is capped at
   `pipeline.thin_jd_score_cap`. The model cannot penalize gaps it cannot
   see, so short snippets otherwise float above fully-described postings.
   The gate is description length alone. It was originally nested inside the
   tiny-denominator branch, which meant a keyword-dense 500-char snippet
   (4-6 extracted phrases, 100% coverage against them) escaped both the
   clamp and the ceiling. On the 2026-07-28 backlog that accounted for 12 of
   the 13 scores at 78 or above, and was the direct cause of the queue
   topping out at 82.

   **Familiar-only-fit cap (Phase 10.2; senior-gated July 2026):** when
   EVERY phrase in `matched` resolves only into `verified.skills_familiar`
   (and not into any Core bucket), the outcome splits by title band:
   senior-band titles cap at 54 with a `decline_reason` (the Java-Developer
   @ Ignite Talent case scored 78 and shipped a Familiar-only-skills resume
   — that misrepresentation pattern stays blocked), while junior/mid titles
   cap at 58 with NO decline, keeping coursework-stack roles visible in the
   55-59 stretch band. Word-boundary matching is used so "Java" doesn't
   match "JavaScript" in the Core bucket.
5. **Cover validator + retry.** `pipeline.cover_validate` catches banned
   phrases, structural violations, and unverified numeric claims.
   `pipeline.cover.write_cover_with_retry` re-prompts up to 3 times with the
   violations as a "fix these" hint before falling back to the last attempt
   (which then ships with audit verdict `revise` so the warnings surface).
6. **Resume↔cover alignment check** (May 2026). `pipeline.audit._alignment_flags`
   scans both artifacts for project anchors mined from `verified.json`
   work history (Atelier Dacko, vintage gaming, HubSpot, Ollama tooling).
   When the cover's middle paragraph anchors on a different verified
   project than the resume's first role's first bullet, the verdict
   downgrades to `revise` (not block). Catches subtle drift the keyword
   coverage check misses: both can technically pass while the artifacts
   still read inconsistent to an AI-screener reading both.

If any check (1)-(3) fails, the apply pipeline aborts for that job rather
than producing a misleading resume. (4), (5), and (6) downgrade rather than block.

## Success criteria

- Pulls fresh GTA jobs daily across configured Greenhouse / Lever / Ashby /
  SmartRecruiters / Workday / Workable / Recruitee / Job Bank CA / RSS /
  Adzuna sources without ToS issues.
- Scores every new job within minutes of ingestion.
- Generates a tailored .docx + cover letter in <90 seconds on local hardware.
- Autofills standard fields on Greenhouse forms, falling back to a generic
  selector-based handler elsewhere.
- Zero cloud API spend at runtime.


## Aggregate analysis (`analyze` command group)

`jobhunt analyze` is a deterministic, LLM-free aggregation surface over the
existing `jobs` table. It mirrors the audit philosophy: regex + counters, no
Ollama calls. Adding an LLM call to any `analyze` subcommand requires explicit
discussion.

### `analyze certs`

Implemented in `src/jobhunt/analyze/certs.py`. The matcher runs a curated
`_KNOWN` list (Cloud, Security, PM/Agile, Data/ML, Networking, Finance)
first, masking consumed character spans, then two generic patterns
(`Certified <Noun>` / `<Noun> certification`) for the long tail. `tally(rows)`
counts each cert once per job regardless of how many times it appears in a JD.
Output: frequency table sorted desc, capped by `--top N` (default 25).

Three modes, all deterministic, with no LLM call at any step:

- **Snapshot** (default): cumulative frequency across all scanned jobs.
- **`--trend`**: bucket by `COALESCE(posted_at, ingested_at)` into two adjacent
  `--window-days` windows (default 30). Render `Prev / Cur / Δ% / Trend` with
  rising / falling / emerging / dropped / stable classification in
  `analyze_cmd._classify`. The current window also feeds a *"Potential new
  certs"* review list pulled from `extract_certs_split`'s generic-regex tier,
  giving the same outcome Gemini-style LLM-discovery would, without an
  Ollama call.
- **`--min-score N`**: joins `scores`, adds a `Fit` column (count restricted
  to jobs you scored ≥ N) and a `Verdict` column derived from
  `analyze_cmd._classify_verdict`. The rubric weighs fit-demand against market
  demand and trend direction so the rightmost column is a one-word decision:
  *Worth pursuing*, *Strong emerging signal*, *Stable staple*, *Skip*,
  *Wrong direction*, *Late — diminishing*, or *Marginal*. Sort priority
  surfaces the actionable rows first.

The decision rubric (`_classify_verdict`) is a small frozen-in-code table,
with no config. Tuning it is a code change, not a runtime knob. Verdict tiers
deliberately favor false negatives (mark "Skip" when in doubt) so the user
isn't pushed to chase certs the data doesn't actually support.
