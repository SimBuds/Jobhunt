# AGENTS.md — Workflow contract for this project

This file is the workflow contract for any AI coding agent (Claude Code, Codex, Cursor, Aider, Cline, Copilot, or any other tool that reads `AGENTS.md`) operating in this repository. It is project-agnostic and intentionally portable — drop this file into the root of any repo and it applies as-is. Project-specific rules go in a `## Project-specific rules` section appended at the bottom.

## Precedence

1. Anything under the `## Project-specific rules` section of *this* file.
2. The workflow contract in the rest of *this* file.
3. The agent's built-in defaults.

If a project-specific rule conflicts with the workflow contract, the project-specific rule wins for that topic only — the rest of the contract still applies. The agent must not silently relax a contract rule; if a project rule isn't explicit, the contract holds.

---

## The Core Documentation Architecture

This project strictly adheres to a 4-pillar documentation system. You must read from and write to these files continuously to maintain context and prevent hallucination. Do not rely on conversational memory.

1. **`AGENTS.md`**: The absolute source of truth for agent behavior, workflow constraints, and project-specific rules. (You are reading it now).
2. **`PLAN.md`**: The high-level blueprint. Contains the full idea of the application, core features, architecture decisions, and scope.
3. **`README.md`**: The developer-facing and user-facing entry point. Explains what the application is, how it works, and how to run it.
4. **`IMPLEMENT.md`**: The execution engine. Contains the granular, phase-by-phase breakdown of tasks, checkboxes for progress, and current state. 

---

## The Workflow Contract

All non-trivial work runs through these five phases. "Non-trivial" is defined by the Blast-radius tiers section below; trivial-tier work skips to Phase 4.

### Phase 1 — Understand & Sync
- Restate the user's request in one sentence.
- **Mandatory Read:** Read `PLAN.md` (to understand the broader feature) and `IMPLEMENT.md` (to see where we are in the execution). Do not guess at project state.
- Identify ambiguity. If the request has ≥ 2 reasonable interpretations, ask before proceeding.
- Read the code paths involved. Do not guess at file contents.

### Phase 2 — Plan & Document
- Update or create `IMPLEMENT.md`. The plan is **never** left just in the conversational context.
- The `IMPLEMENT.md` file must list the **phases**, each with: a goal sentence, files to touch, functions to add/change, and verification steps.
- Surface the **reuse audit** (see Reuse-first rule) for every new function/class/component proposed.
- Ask the user to approve the updated `IMPLEMENT.md` before any code is written.

### Phase 3 — Phase the Work (Context Anchoring)
- Break the plan into phases that each pass the Phase-sizing rules below.
- At the start of Phase 3 — and at the start of *every* subsequent phase — check `IMPLEMENT.md` to verify current state.
- Re-state in 3–6 bullets:
  - The inherited decisions (every choice the user has made so far in this session).
  - The current state based on `IMPLEMENT.md`: phases done, phase in progress, phases remaining.

### Phase 4 — Execute One Phase
- One phase at a time. No look-ahead edits into later phases.
- Honor the Surface-first audit. Touching a file or function not explicitly listed in the current phase of `IMPLEMENT.md` is a fatal scope error.
- If a decision arises mid-phase that wasn't covered by the plan, stop and ask. Do not silently choose.

### Phase 5 — Verify & Hand Back
- Run the verification listed in `IMPLEMENT.md` for this phase. Report observed output, not predicted output.
- Satisfy the **Definition of Done** (below) before claiming completion.
- End the turn with the literal handoff line, and **no tool calls after it**:

  > `Phase <N> complete. Do I have approval to begin Phase <N+1>?`

  On the final phase, use:

  > `Phase <N> complete. Do I have approval to mark this work complete?`

  Pauses without this line count as incomplete phases. This is the only sanctioned way to yield control.

---

## Phase-Sizing Rules

A phase is "small enough" only if **all** of the following hold. If any fails, split the phase in `IMPLEMENT.md`.

- **One-sentence test.** The phase's goal fits in one declarative sentence with no "and". If you need "and", that's two phases.
- **Diff-surface budget.** ≤ ~300 lines changed, ≤ 5 files touched, ≤ 1 new public interface. Defaults, not hard limits — exceeding any requires an explicit note in the plan justifying why splitting is worse.
- **Single test plan.** Verification fits in ≤ 3 bullets. If you need 5 bullets to describe what to test, the phase is doing too much.
- **Atomic revert.** The phase is a single commit that can be reverted without breaking the build or leaving the repo half-done.
- **Walking-skeleton bias.** The first phase delivers the thinnest possible end-to-end path, even if shallow. Later phases thicken. Don't build all of layer A before any of layer B.
- **Surface-first audit (hard stop).** Before writing code, list the files you will touch and the functions you will add/change. Touching anything outside that list is a fatal scope error: immediately revert the unplanned change, pause execution, and ask for permission to expand the surface.
- **No piggybacking.** A phase does its one thing. Refactors, drive-by cleanups, "while I'm here" fixes go into their own phases.

---

## Reuse-First Rule

Before introducing a new utility, class, component, or helper, run a concrete search (`grep`, `rg`, or equivalent) for existing implementations in the project and in any referenced shared libraries. In the plan, state:
- (a) the exact search terms used,
- (b) the candidates found,
- (c) why each candidate cannot be reused.
"I didn't see one" is not a valid answer. The search itself must be shown.

---

## Definition of Done (Per Phase)

A phase is strictly incomplete until **all** of the following are true:

1. The code change matches the planned diff surface in `IMPLEMENT.md` — no extras.
2. New behavior has at least one test that fails without the change and passes with it (or manual E2E output is reported).
3. Existing tests still pass, or you have explicitly enumerated which broke and why.
4. **The 4-Pillar Documentation Check (Critical Step):** - **`IMPLEMENT.md`** must be updated to check off the current phase and log any deferred work as new phases.
   - **`PLAN.md`** must be updated if the architecture, core data structures, or feature scope changed during the phase.
   - **`README.md`** must be updated if running instructions, env vars, or developer/user-facing APIs changed.
   - *Code shipped without updating the relevant markdown files fails the Definition of Done.*
5. You have posted a phase report: *what changed, what was tested, what docs were updated, what was deferred*. (Deferred items go into `IMPLEMENT.md` as follow-up phases, never as `TODO` comments in code).
6. The user has approved before the next phase begins.

---

## Decision Gates — When to Stop and Ask

You **MUST** ask, not assume, when:
- The user's request has ≥ 2 reasonable interpretations and the choice affects the diff.
- A naming, data-shape, or API-shape decision will be load-bearing for later phases.
- The change crosses into the **risky** blast-radius tier.
- You discover mid-phase that the `IMPLEMENT.md` plan was wrong. Surface the discovery and re-plan; don't silently adapt.

You **MAY** proceed without asking when:
- The change is trivial-tier and reversible by a single `git revert`.
- The user has already answered the same question in this session or in this `AGENTS.md` file.

When in doubt, present the options as a multiple-choice question with a recommended default and the tradeoff for each. Do not invent a single path forward when a meaningful fork exists.

---

## Blast-Radius Tiers

- **Trivial** — single-file, ≤ 20 lines, no public API change, no shared-state effect. Examples: typo fixes, comment edits, renaming a local variable. Proceed and report in one sentence.
- **Standard** — multi-file or new function, but contained to one module. Tests run locally. Use the full phase contract: plan → execute one phase → verify → update docs → hand back for approval.
- **Risky** — destructive ops (`rm -rf`, `git reset --hard`, force-push), schema/migration changes, dependency upgrades, CI/CD edits, modifications to shared infrastructure. Stop and ask before *each* such action, even within an approved plan.

---

## Anti-Patterns (Strictly Prohibited)

- "While I was in there I also…" — scope creep. Defer or split.
- "I'll add a TODO for that" — silent debt. Put it in `IMPLEMENT.md` as a phase.
- "The tests probably still pass" — run them.
- "I'll mock this for now" — say so loudly; mocks default to phase-end removal.
- "I'll document it later" — updating `PLAN.md`, `README.md`, and `IMPLEMENT.md` is part of the code commit. 
- Ending a phase without the literal handoff line.
- Bundling a refactor into a bugfix, or a bugfix into a feature.

---

## Notes on Tone

Keep responses tight. State results and decisions directly. Don't narrate internal deliberation. The phase report and the handoff line are the contract — everything else is optional.

---

## Project-Specific Rules

*Everything above this line is the shared workflow contract and should not be edited per-project. Add project-specific guidance below — stack, build/test commands, conventions, paths to other docs, domain rules.*

### Documentation map

This repo predates the portable contract above; its design docs map onto the
4-pillar system as follows, plus a few project-only references. Precedence is
governed by the **Precedence** section above — in particular, if `PLAN.md`
contradicts a rule in this file, this file wins; open a PR to reconcile rather
than working around it.

- `AGENTS.md` (this file) — pillar 1: agent guardrails and conventions. The *how*.
- `PLAN.md` — pillar 2: design rationale. The *why* decisions were made.
- `README.md` — pillar 3: install + usage for developers running the app locally.
- `IMPLEMENT.md` — pillar 4: the execution engine. Phase-by-phase task breakdown, progress checkboxes, current state.
- `Resume_Tailoring_Instructions.md` — honest-tailoring rules (no fabrication, ATS-safe formatting, auto-decline triggers). Mirrored at `kb/policies/tailoring-rules.md` for prompt injection.
- `kb/README.md` — what lives under `kb/` and how each subdirectory is maintained.
- `kb/seeds/gta-employers.toml` — curated verified ATS slugs imported by `jobhunt config seed --apply`. Edit via `scripts/verify_seeds.py`, never hand-add unverified entries.
- `CLAUDE.md` — tiny stub that `@`-imports this file so Claude Code's auto-load still works. Don't edit it; edit this file.

### Documentation style

When writing or updating any human-facing markdown doc (`README.md`,
`PLAN.md`, `IMPLEMENT.md`, `kb/README.md`, and the like), keep prose
punctuation plain:

- **No em dashes or en dashes (`—`, `–`) in sentences.** Recast with a
  period, comma, colon, or parentheses, whichever fits the clause.
- **No semicolons in prose.** Split into two sentences, or join with a
  comma + conjunction.
- These two rules apply to **prose only**. Leave code blocks, inline-code
  spans, config-value literals (e.g. a `user_agent` string), and shell/TOML
  comments untouched — their punctuation is load-bearing.
- This style rule does not apply to this file's own existing headings; it
  governs new and edited prose going forward.

## What this project is

A local-first CLI tool for personal job search automation. Pulls jobs from public ATS APIs, runs fit-scoring and document tailoring against the user's profile using local Ollama models, and assists with form autofill via Playwright (human submits, never the bot).

## Hardware context

- Arch Linux, Ryzen 9 5900, 32GB DDR4, RTX 3080 (10 GB VRAM total). Arch idles around 1.5 GB on the GPU, so `OLLAMA_GPU_OVERHEAD` is intentionally **not** set — the full 10 GB is available to Ollama and the active model lands at ~9.1 GB resident with comfortable headroom.
- Ollama at `http://localhost:11434`
- Default model: base **`qwen3.5:9b`** (2026-05-28). The gateway always sends its own system message (the task prompt from `kb/prompts/`), which overrides any Modelfile SYSTEM at runtime, *and* its own options (`gateway.client._DEFAULT_OPTIONS`), which override the Modelfile PARAMS — so behavior is fully defined in-repo and no custom Modelfile is needed. **The load-bearing app-owned option is `num_ctx=16384`** (NOT a renderer/parser concern, as was first assumed): these prompts run ~6k+ tokens, Ollama's default context is 4096, and `OLLAMA_CONTEXT_LENGTH` is *not* set on this box — so without an explicit `num_ctx` the prompt silently truncates to 4096, the JSON-schema instruction falls off the end, and the model emits prose instead of JSON. Pinning `num_ctx=16384` in the gateway is what makes bare `qwen3.5:9b` work (verified 2026-05-28: bare qwen + `num_ctx=16384` returns valid JSON). All task slots (score, tailor, cover, answer) run the same hot model — single-model-per-scan, no intra-scan reload churn — at the gateway-pinned `num_ctx=16384` (keep `MAX_DESC_CHARS`/`MAX_POLICY_CHARS` in `pipeline.score` aligned) with `keep_alive=-1` (per-call override that pins the model in VRAM during active work; the systemd default `OLLAMA_KEEP_ALIVE=10m` handles idle unload between scans) and reasoning (`think`) disabled at the gateway. `nomic-embed-text` reserved for future embeddings. QA is deliberately deterministic (see `pipeline.audit`) — no LLM QA slot.
- Ollama systemd env (Arch, `sudo systemctl edit ollama.service`):
  ```
  Environment="OLLAMA_KV_CACHE_TYPE=q5_0"      # q5_0 KV cache cuts VRAM ~30% vs default; preferred over q8_0 to leave headroom alongside Q4_K_M weights on a 10 GB card
  Environment="OLLAMA_FLASH_ATTENTION=1"       # required to use a quantized KV cache
  Environment="OLLAMA_NUM_PARALLEL=1"          # single concurrent request — matches our sequential pipeline
  Environment="OLLAMA_CONTEXT_LENGTH=16384"    # 16k context server-side fallback; the gateway ALSO pins num_ctx=16384 per call (app-owned, load-bearing — see LLM rules 4/5a). Keep the two aligned.
  Environment="OLLAMA_KEEP_ALIVE=10m"          # idle unload after 10m; per-call keep_alive=-1 from gateway pins model during active scans
  Environment="OLLAMA_MAX_LOADED_MODELS=1"     # one model in VRAM at a time
  ```
  Changing any of these requires updating the matching gateway-level value (or vice versa) so JD truncation thresholds and the cold-start budget stay aligned.
- One model hot in VRAM at a time. Single-model setup eliminates reload churn between task types; reload churn was a major source of scan freezes prior to the May 2026 consolidation.

## Stack

- Python 3.12+ managed with `uv` (not pip, not poetry)
- `typer` for CLI (subcommand-friendly, type-driven)
- `httpx` for HTTP (async, sane defaults)
- `pydantic` v2 for models and config
- `sqlite3` via stdlib + plain SQL migrations in `migrations/`. No ORM.
- `playwright` for browser automation
- `pytest` + `pytest-asyncio` for tests
- `ruff` for lint + format. `mypy --strict` on `src/`.

## Conventions

**Package manager.** Always `uv add`, `uv sync`, `uv run`. Do not write `pip install` in any docs or scripts.

**Errors.** Use specific exception types from `jobhunt.errors`. Do not raise bare `Exception`. CLI commands catch their domain errors and exit with informative messages, never tracebacks (unless `--debug`).

**Config.** Single source of truth: `~/.config/jobhunt/config.toml`, schema validated by Pydantic. Env vars override (prefix `JOBHUNT_`). Never hardcode paths, model names, API keys.

**Secrets.** API keys (Adzuna, USAJobs) live in `~/.config/jobhunt/secrets.toml` (mode 0600) or env vars. Never in code, never in commits, never in logs.

**Database.** SQLite at `data/jobhunt.db`. Migrations are numbered SQL files in `migrations/`. Run on `jobhunt db migrate`. Never use an ORM. Write plain parameterized SQL.

**LLM calls.** Always go through `jobhunt.gateway`. Never instantiate an OpenAI/Ollama client directly elsewhere. The gateway handles model selection, prompt composition, retries, and JSON-schema enforcement.

**Prompts live in `kb/prompts/`** as markdown. Never inline prompt strings in Python source longer than 5 lines. The prompt loader composes them with profile data at call time.

**Knowledge base is read-only at runtime.** Never write to `kb/` from running code. It's edited by the human; the app only reads.

**Async by default for I/O.** All HTTP and disk-heavy operations are async. CLI commands use `asyncio.run` at the entry point.

**Logging.** `structlog` to stderr. `--verbose` raises level. Never log full prompts or full responses at INFO; use DEBUG with truncation.

## Project structure

The package is named `jobhunt` (legacy — kept to avoid churn). The CLI script
is `jobhunt`.

```
src/jobhunt/
├── cli.py                     # Typer app, subcommand wiring only
├── commands/
│   ├── convert_resume_cmd.py  # P1
│   ├── scan_cmd.py            # P2: ingest + score + cross-source dedupe
│   ├── apply_cmd.py           # P3+P4: tailor + cover + audit + autofill
│   ├── add_cmd.py             # URL → ATS slug → config.toml (primary slug-acquisition surface)
│   ├── answer_cmd.py          # P11: application-form question assistant
│   ├── list_cmd.py            # P5: pipeline view + weekly rollup
│   ├── discover_cmd.py        # legacy: harvest URLs + probe Greenhouse/Ashby/Lever/SmartRecruiters
│   ├── _config_write.py       # atomic `.bak`-then-tmp-rename helper (shared by add, config seed, discover --apply)
│   ├── db_cmd.py              # hidden internal
│   └── config_cmd.py          # `config seed`, `config show`, `config calibrate`
├── resume/
│   ├── parse_docx.py          # baseline .docx → verified.json + kb/profile/*.md
│   └── render_docx.py         # tailored markdown → ATS-safe .docx
├── ingest/                    # one file per source
│   ├── _filter.py             # GTA allowlist + Remote-Canada heuristic
│   ├── _rss.py                # stdlib RSS/Atom parser (no extra deps)
│   ├── greenhouse.py
│   ├── lever.py
│   ├── ashby.py
│   ├── adzuna_ca.py
│   ├── smartrecruiters.py     # SmartRecruiters public Posting API (no key needed)
│   ├── workable.py            # Workable public widget API (no key needed)
│   ├── recruitee.py           # Recruitee public offers API (no key needed)
│   ├── job_bank_ca.py         # Government of Canada Job Bank (HTML search-results scraper; RSS is dead)
│   ├── rss_generic.py         # generic employer career RSS/Atom feeds
│   └── manual.py              # --url: ad-hoc single-JD synth into a Job
├── gateway/                   # Ollama client + prompt loader
│   ├── client.py              # complete_json (POST /api/chat with format=schema)
│   └── prompts.py             # frontmatter-aware markdown prompt loader
├── analyze/                   # deterministic aggregations over the jobs DB (no LLM)
│   └── certs.py               # cert keyword extractor + per-job tally
├── discover/                  # URL-extract + ATS-API probing for slug acquisition
│   ├── slug_candidates.py     # pure name→slug normalizer (staffing-agency filter)
│   ├── url_extract.py         # deterministic URL → (ats, slug, site, host) parser
│   └── probe.py               # async Greenhouse/Ashby/Lever/SmartRecruiters probe + slug_probes cache
├── pipeline/                  # score, tailor, cover, audit, cover_validate, answer
│   ├── score.py
│   ├── tailor.py              # enforces no-fabrication invariants
│   ├── cover.py
│   ├── cover_validate.py      # deterministic cover-letter validator (banned phrases, etc.)
│   ├── audit.py               # post-generation audit: keyword coverage + verdict
│   └── answer.py              # application-form question pipeline (reuses cover validators)
├── browser/
│   ├── autofill.py            # headed Playwright session, fill-plan.json
│   ├── profile_map.py         # ApplicantProfile → form key map
│   └── handlers/              # ATS-specific handlers + generic fallback
├── http.py                    # async httpx client + per-host rate limiter
├── secrets.py                 # ~/.config/jobhunt/secrets.toml loader
├── config.py                  # config loading, Pydantic models
├── db.py                      # connection + migration runner + query helpers
├── errors.py
└── models.py                  # Pydantic domain models (Job, Score, Application)
```

## Commands

User-facing surface is **ten** commands. `db` and `config` are hidden internals
(except `config seed`, which is part of the user-facing onboarding flow).

```
jobhunt setup                # guided first-run wizard: db init + convert-resume +
                             # applicant defaults (years_experience,
                             # include_senior_roles, salary, work arrangements,
                             # employment types) + config seed import. Safe to
                             # re-run for updating applicant defaults — each
                             # step detects existing state.
jobhunt convert-resume       # parse baseline .docx → kb/profile/
jobhunt scan                 # ingest GTA jobs + score
jobhunt apply <job-id>       # tailor + cover + autofill (you submit)
jobhunt apply --top N        # auto-pick N best-fit unapplied (1..10)
jobhunt apply --best         # interactive picker over top 10
jobhunt apply --url <URL>    # ad-hoc: fetch one JD, score, tailor; prints `add` suggestion
jobhunt add <URL>            # parse URL → write ATS slug to config.toml
jobhunt answer "<question>" [--recall]
                             # draft a tailored response to a form question;
                             # with --recall, treats the argument as a phrase
                             # and lists past saved answers whose question
                             # text contains it (case-insensitive).
jobhunt interview-prep <id> [--stage agency|hiring_manager|assessment] [--research]
                            [--refresh-research]
                            [--recruiter-type internal_recruiter|hiring_manager|external_agency|unknown]
                             # hybrid prep doc: deterministic skeleton + LLM middle
jobhunt list [--week N] [--verdict ship|revise|block] [--no-reply]
             [--older-than 14d|2w]
                             # pipeline view + weekly rollup. `--verdict`
                             # filters by audit verdict (reads audit.json).
                             # `--no-reply` shows applied jobs without a
                             # recorded recruiter response. `--older-than`
                             # narrows to applications submitted before
                             # now-duration (e.g. 14d nudge list).
jobhunt analyze certs [--top N] [--trend] [--window-days N] [--min-score N]
jobhunt analyze skills --gaps [--window-days N] [--top N]
                             # tech tokens over-represented in declined vs
                             # accepted JDs over the window. Deterministic
                             # regex scan, no LLM.
jobhunt analyze employers --hiring-velocity [--window-days N]
                             # post counts per configured slug; surfaces
                             # configured-but-zero-posts entries as reprobe
                             # candidates.
jobhunt analyze validators [--window-days N] [--top N]
                             # which cover-letter validators fired most in
                             # audit.json files over the window. Use to
                             # find over-broad rules to prune.
jobhunt analyze response-rate [--by score|ats]
                             # interview/response rate per bucket (score
                             # band or ATS source). Reads Phase 1's
                             # response_received_at + post-applied statuses.
                             # cert frequency, trends, and fit verdicts
jobhunt discover slugs       # legacy: harvest URLs in jobs DB + probe Greenhouse/Ashby
jobhunt config seed --apply  # import kb/seeds/gta-employers.toml into config
jobhunt config reprobe [--prune] [--force]
                             # re-probe every configured greenhouse/lever/
                             # ashby/smartrecruiters slug; print live vs
                             # stale. --prune removes stale entries (with
                             # confirmation unless --force). Workday is
                             # skipped — CXS handshake isn't a cheap probe.
```

`interview-prep` is the post-conversion companion to `apply`. When a job
moves to `interviewing` (via `apply --set-status interviewing`), the apply
command prints a nudge pointing at this command. Hybrid generation:
deterministic skeleton owns the header + comp heads-up + pre-call
checklist + after-the-call footer; one structured LLM call produces the
role decode, strongest anchors, likely questions (with answer beats),
questions to ask back, and honest gaps (with non-defensive reframes).

Honesty enforcement reuses existing infrastructure — no new validators:
- `cover_validate` banned phrases / defensive patterns / fabrication
  watchlist / unverified-numbers run against the concatenated LLM output.
- A separate anchor-authenticity check requires each anchor to contain
  at least one substantive token (alphabetic, length ≥ 5) appearing
  verbatim in the verified blob (skills + work history + summary). This
  rejects fabricated anchors like "Built Kubernetes clusters" while
  accepting full sentences like "Built a 14+ page Shopify storefront for
  Atelier Dacko" where stop words like "for" wouldn't survive the strict
  identity-subset rule the tailor uses for skills.

Retry loop mirrors `write_answer_with_retry` (Phase 9.2 pattern: forces
`temperature=0` on attempts 2+). Stage value tunes prompt emphasis;
re-running with a different `--stage` overwrites the same single file
at `data/interview-prep/<job-id-safe>.md`. Opt-in `--research` fetches
the JD URL and company root (robots-checked; `--force-robots` overrides
for personal-use only) and attaches stripped HTML to the prompt.

`answer` drafts a response to a single application-form question using the
same honesty rules as the cover-letter pipeline (banned phrases, fabrication
watchlist, defensive-pattern regex, unverified-number guard). Reuses
`cover_validate`'s core check helpers via `pipeline.answer.validate_answer`,
dropping the cover-only structural rules (salutation, sign-off, paragraph
count, company-in-lead). Retry loop mirrors `write_cover_with_retry` and
forces `temperature=0` on attempts 2+ (Phase 9.2 pattern).

Two modes:
- `jobhunt answer "..."` — standalone, no JD context.
- `jobhunt answer "..." --job <id>` — loads JD title/company/description from
  the `jobs` table and injects it into the prompt.

Output: prints the answer to stdout for paste-to-form, AND saves a markdown
artifact at `data/applications/<id>/answers/<sha1>.md` (job-scoped) or
`data/answers/<sha1>.md` (standalone). Filename is a 12-char sha1 of the
question text — re-running the same question overwrites the same file.
Use `--no-save` to skip the artifact and only print to stdout.

Length default: `cfg.pipeline.answer_max_words` (200). Override per call
with `--max-words` (use 60 for "years of experience"-style factual
questions, 250 for STAR-style behavioural ones).

`analyze` is a deterministic, LLM-free aggregation surface — do not add an
Ollama call to any `analyze` subcommand without explicit discussion. It mirrors
the audit philosophy: regex + counters over existing DB rows, no network I/O.

`analyze certs` has three modes, all deterministic:
- Snapshot (default): cumulative cert frequency across `jobs`.
- `--trend`: two-window delta keyed on `COALESCE(posted_at, ingested_at)`. The
  current window also produces a "Potential new certs" review list from the
  generic-regex tier in `extract_certs_split` — manual-promotion feedback loop
  for `_KNOWN`, no LLM. Trend label rubric lives in `analyze_cmd._classify`.
- `--min-score N`: joins `scores`, adds a `Fit` column + per-cert `Verdict`
  (`Worth pursuing` / `Skip` / `Wrong direction` / etc.) from
  `analyze_cmd._classify_verdict`. The verdict rubric is frozen in code —
  tuning it is a code change, not a runtime knob. Adding any LLM call here
  needs explicit sign-off; the verdict is the *whole point* of the command
  staying deterministic and audit-traceable.

`discover slugs` reads distinct companies from the jobs DB (sorted by post
count), normalizes each name via `discover.slug_candidates.candidates()` to up
to 3 candidate slugs, then probes the Greenhouse / Ashby / Lever /
SmartRecruiters / Workable / Recruitee public APIs. Hits are printed;
`--apply` appends them to `config.toml` (after writing a `.bak`). Misses
persist to the `slug_probes` table with a 90-day TTL — older misses
re-probe automatically without `--include-cached`. Staffing-agency names
are filtered out at the candidate stage — they never run public ATS
boards. Per-host rate limit + per-company 15 s timeout + bounded
concurrency keep wall time predictable; `--limit 100` is the default run
cap.

**Auto-discovery in `scan` (May 2026).** `cfg.ingest.auto_discover`
(default true) runs the same `discover.probe.discover()` machinery at the
end of every `scan` that inserted new rows. Hits are appended to
`config.toml` via the shared `commands._config_write.write_config_atomically`
helper so the next scan ingests those slugs natively for deep JDs. Toggle
off per-run with `jobhunt scan --no-discover` or globally via
`[ingest] auto_discover = false`. The seed file
`kb/seeds/gta-employers.toml` is now a cold-start aid only — daily use
self-bootstraps.

`apply --url` is a user-initiated single-shot fetch. It synthesizes a
`Job(source="manual", id="manual:<sha1-12>")`, upserts it into the DB so it
shows up in `list` and re-applies are idempotent, then runs the normal
tailor/cover/audit pipeline. `--no-score` skips the score pass (audit's
coverage falls back to the title/JD intersect). `--force-robots` overrides
the robots.txt check — personal-use single-shot only. After the pipeline
completes, `_maybe_suggest_add` runs `url_extract` on the input URL and
prints a `jobhunt add` nudge if the URL points at a recognized, ingestable
ATS whose slug isn't already in config. Suppressed for iCIMS (recognized
but not yet ingestable) and for already-configured slugs.

`add` is the URL-first slug-acquisition path. Accepts any URL recognized by
`url_extract` (Greenhouse, Lever, Ashby, SmartRecruiters, Workday), probes
once to confirm (skipped for Workday — CXS handshake isn't worth the wiring),
then appends to the matching `cfg.ingest.*` list via the shared
`commands._config_write.write_config_atomically` helper. iCIMS URLs exit with
code 2 and a "coming soon" message rather than being silently dropped.

`config seed` reads `kb/seeds/gta-employers.toml` and additively merges
verified slugs into `config.toml`. The seed list is **read-only at runtime**
and only updated through `scripts/verify_seeds.py`, which probes every
candidate before they're committed — this is what prevents shipping stale
slugs (Shopify, 1Password, etc., which moved off Greenhouse and now 404).
`--apply` requires explicit invocation; bare `jobhunt config seed` errors.

All three writers (`discover slugs --apply`, `add`, `config seed --apply`)
share `commands._config_write.write_config_atomically`. The helper produces
a `.bak` snapshot then atomically renames a `.tmp` over the original, but
**inline comments in `config.toml` are dropped** (tomli_w is not
comment-preserving). Surface this in command output near any programmatic
write so the user isn't surprised. The README repeats the warning at the
config section.

Subcommand groups map to modules in `commands/`. Keep `cli.py` to wiring only.

**Hidden internals:**
- `jobhunt db init|migrate|reset` — `reset` wipes DB, `data/applications/`,
  `data/cache/`, the Playwright profile, **and** `kb/profile/`, then re-runs
  migrations. Use `--force` to skip the confirmation prompt.
- `jobhunt config show|path|calibrate`.

**Profile guard.** `scan`, `list`, and `apply` call `ensure_profile(cfg)` from
`commands/__init__.py` at the top of their callbacks. If
`kb/profile/verified.json` is missing, they exit with a friendly message
pointing the user to `convert-resume`. Do not bypass this guard — adding new
top-level commands that touch scoring/listing/applying must call it too.

## Ingestion rules — non-negotiable

1. **Public APIs only** *(one sanctioned exception: Job Bank — see below)*. Greenhouse `boards-api`, Lever `api.lever.co/v0`, Ashby posting API, Adzuna CA (with API key), SmartRecruiters public Posting API (`api.smartrecruiters.com/v1/companies/{slug}/postings`, no key), Workable widget API (`apply.workable.com/api/v1/widget/accounts/{slug}`, no key), Recruitee offers API (`{slug}.recruitee.com/api/offers/`, no key), generic RSS.
   - **Job Bank Canada (HTML-scrape carve-out, 2026-06).** Job Bank's public RSS is dead — `format=rss` returns the HTML page and `/jobsearch/feed/jobSearchRSSfeed` serves an empty `<feed>` even with a valid session. The adapter (`ingest/job_bank_ca.py`) therefore parses the HTML search-results page. This is sanctioned because Job Bank is a Govt-of-Canada *public job-search service*, its robots.txt has **no `Disallow`** (only `Crawl-delay: 5`), and it is not a ToS-restricted board like the rule-3 sites. `scan_cmd` passes a dedicated `RateLimiter(0.2)` (5 s spacing) to honor the crawl delay. Config holds full search URLs (`[ingest] job_bank_ca = [...]`), not slugs. Do NOT generalize this carve-out to any other site — it is specific to Job Bank's public-service + robots-clean + dead-API conjunction.
2. **GTA scope.** Filter by GTA city allowlist (Toronto, Mississauga, Brampton, Hamilton, Oakville, Markham, Vaughan, Burlington, Oshawa, Richmond Hill, Pickering, Ajax, Whitby, Milton, North York, Scarborough, Etobicoke, plus the KW corridor — Waterloo / Kitchener / Cambridge / Guelph — and Barrie) **plus Remote-Canada** postings. Adzuna uses `where=Toronto&distance=100&country=ca`. Drop everything else. **May 2026:** weak Canada hints (`EST`, `Eastern Time`, comma-delimited `ON`) only accept when the same string has no non-Canada anchor (`US`, `EMEA`, etc.) — US-Eastern remote roles were sneaking through before the tightening.
3. **No LinkedIn, no Indeed, no Glassdoor scraping**, ever. Even if the user asks. Push back and explain.
4. **Respect `robots.txt`** for any non-API HTTP fetch. The `--url` ad-hoc path checks via stdlib `urllib.robotparser` and accepts `--force-robots` for personal-use override only; this carve-out does **not** apply to `scan` ingest adapters. (this file historically called for `protego`; the project hasn't taken that dep yet — stdlib is the current implementation.) The Job Bank HTML-scrape adapter (rule 1) is robots-clean by inspection (no `Disallow`) and honors the requested `Crawl-delay: 5` via a dedicated limiter — verified 2026-06; re-check if Job Bank publishes a `Disallow` for `/jobsearch/`.
5. **Rate limits:** 1 req/sec/host default. Exponential backoff on 429/5xx.
6. **User-Agent:** identifies the tool and provides a contact, e.g. `jobhunt/0.1 (+personal-use; your-email@example.com)`. Set via `config.toml` under `[ingest] user_agent`.
7. **Cache** raw responses to `data/cache/` with a TTL; don't re-hit APIs needlessly during dev.
8. **Adzuna queries auto-derive from `verified.json`** when `cfg.ingest.adzuna.queries` is empty. `ingest._query_planner.derive_adzuna_queries` walks `skills_core` / `skills_cms` / `skills_familiar` plus work-history bullets and emits up to 10 role-suffixed queries (capped to keep budget at ~30 API calls/scan with `pages=3`). Umbrella triggers (`cms developer`, `ai engineer`, `seo specialist`) fire on bucket-presence / bullet-token signals. Populated `queries` list bypasses the planner entirely. Adding new skill buckets to verified.json requires extending `_SKILL_QUERIES` or `_CATEGORY_TRIGGERS` to surface them.
9. **Pre-score chokepoint filters** at `commands.scan_cmd._ingest_all`'s drain loop (applied after dedupe, before `upsert_job`). All filters are pure and adapter-agnostic; each reports its drop count in the per-scan summary:
   - **Management-title drop** via `ingest._filter.is_management_title` — regex matches Manager / Director / Head of / VP / Vice President / Chief X Officer. **Does NOT** match Senior / Lead / Staff / Principal / Architect — those are handled separately by `is_senior_title` below.
   - **Research/ML-title drop** via `ingest._filter.is_research_title`, opt-in via `cfg.ingest.drop_research_titles` (default False). Matches Applied / ML / AI / Research / Quant scientist|engineer|researcher + Data scientist|engineer|platform. Enable for frontend / CMS / full-stack profiles where these roles are never a fit.
   - **Non-engineering-title drop** via `ingest._filter.is_non_engineering_title`, gated by `cfg.ingest.drop_non_engineering_titles` (**default True** — unlike research, non-eng functions never fit any eng profile). Curated high-precision *function* terms (Account Executive, Office Administrator, Sanitation, Food Safety/FSQA, Maintenance Technician, Buyer/Procurement/Supply, Production Supervisor, Legal Counsel, Recruiter, Performance Marketing, security guard, etc.) — large Workday tenants post their whole org, so these used to each cost a full LLM score. **Deliberately excludes** ambiguous tokens (`analyst`, `associate`, bare `specialist`/`coordinator`, `engineer`, `security`); a dev/eng signal (`_ENG_GUARD_RE`: software / developer / devops / front-end / back-end / full-stack / data|platform|cloud|qa|security engineer / etc.) **always wins** so a real role is never dropped. Validated 2026-06 against the live DB: 25/167 dropped, **0 false positives** among score ≥55 roles.
   - **Senior-title drop** via `ingest._filter.is_senior_title`, gated by `cfg.applicant.include_senior_roles` (default True). When False, drops Senior / Sr. / Lead / Staff / Principal / Architect titles. Captured via the `jobhunt setup` wizard as a yes/no preference — no YoE inference is applied at ingest. Independent of `applicant.years_experience` (which feeds the score prompt, not the ingest filter).
   - **Freshness window** via `ingest._filter.is_within_age_window` — `cfg.ingest.max_age_days` (default 7) caps how stale a `posted_at` can be. CLI override via `jobhunt scan --max-age-days N`; 0 disables. As of Phase 5 the Workday adapter parses `postedOn` prose ("Posted 3 Days Ago" / "Yesterday" / "Today" / "30+ Days Ago") into a timestamp so Workday rows now respect the window; any future adapter that still can't infer a posted-at passes through.
10. **Workday adaptive GTA scan** (`ingest.workday._scan`, May 2026). Workday's CXS `/jobs` endpoint has no server-side GTA filter, so the adapter applies `is_gta_eligible` client-side. A blank `searchText=""` scan only walks the first 100 postings (`max_pages=5 × _PAGE_LIMIT`); on large global tenants (NVIDIA ~2000, Live Nation ~1417, Capital One ~1571 postings) the handful of GTA roles sit past offset 100 and were silently missed. `_scan` now reads `total` from one probe page: boards `<= _BLANK_SCAN_MAX` (200) keep the blank walk (Canada-centric tenants like TD/BMO/Moneris surface GTA roles early — zero behavior change); larger boards issue a deduped union of `_GTA_SEARCH_TERMS` (`"Toronto"`, `"Ontario"`, `"Remote, Canada"`) so GTA roles land in the scanned window. `"Canada"` is deliberately excluded — it matched every posting on some tenants (boilerplate). `is_gta_eligible` is still the precision gate in both branches. Tuning lives in the two module constants (promote to `cfg.ingest` only if per-region tuning is ever needed). The reprobe/discover skip for Workday (CXS isn't a cheap probe) is unchanged.

## Browser automation rules — non-negotiable

1. **Never click a submit button.** Fill fields, then hand off to the human. The user is in the loop on every application.
2. **Never auto-create accounts** on employer sites. If signup is required, exit and tell the user.
3. **Log a field-fill plan** to `data/applications/<job-id>/fill-plan.json` before executing it, for auditability.
4. **Run headed by default.** Headless only if `--headless` flag and only for dry-runs.
5. **No stored employer credentials.** If a site requires login, the user logs in manually each time.

## LLM call rules

1. **Every structured call uses a JSON schema.** `gateway.client.complete_json(schema=...)` posts to Ollama `/api/chat` with `format: <schema>`. No free-form JSON parsing.
2. **Reasoning disabled.** The gateway sends `"think": false` so qwen3.5's
   reasoning trace doesn't blow past the timeout on structured calls. Quality is held by the deterministic
   post-processing layers (score clamp, cover validator + retry, audit), not
   by reasoning tokens. If a future task slot needs thinking, plumb it
   through as a per-call kwarg — don't flip the default.
3. **Keep-alive + warm-up.** `keep_alive=-1` in the payload pins the model in
   VRAM for the duration of an active run. The systemd-level
   `OLLAMA_KEEP_ALIVE=10m` is the idle-unload fallback between scans, but the
   per-call value is what Ollama uses while a request is in flight, so the
   model never drops mid-pipeline. `scan_cmd._warm_model()` fires a tiny chat
   before the scoring loop so the first real call doesn't pay cold-load on
   top of the 240 s gateway timeout.
4. **Context length is app-owned** (2026-05-28). The gateway pins
   `num_ctx=16384` in `_DEFAULT_OPTIONS` and sends it on every call. This is
   deliberate: `OLLAMA_CONTEXT_LENGTH` is not reliably set on this box, Ollama's
   default is 4096, and the score/tailor prompts run ~6k+ tokens — so relying on
   the server env silently truncated prompts to 4096 and the model emitted prose
   instead of schema JSON. The score/tailor pipelines truncate description to
   `MAX_DESC_CHARS=16000` and policy to `MAX_POLICY_CHARS=6000` — see
   `pipeline.score`. If you change the pinned `num_ctx`, bump these in step so
   the prompts use the room rather than overflowing it.
5. **Default temperatures** are set in prompt frontmatter: scoring 0.0, tailoring 0.3, cover letters 0.7 (the cover prompt is tuned around the wider creative latitude — don't drop it back to 0.5 without re-tuning the anti-pattern rules).
5a. **Options are app-owned** (2026-05-28). The gateway pins
   `gateway.client._DEFAULT_OPTIONS` (`num_ctx=16384, num_predict=4096,
   top_p=0.95, top_k=20, min_p=0, presence_penalty=0`) on every call so
   structured-task behavior is defined in-repo, not by the model's Modelfile or
   server env. `num_ctx=16384` is the load-bearing one (see item 4 — without it
   prompts truncate to 4096 and the model emits prose). `presence_penalty=0`
   drops qwen3.5:9b's `1.5` chat/thinking anti-repeat default, which fights the
   repeated tokens structured JSON needs (field names, the verbatim JD keywords
   the tailor must echo). Together these are what let jobhunt run bare
   `qwen3.5:9b` instead of a custom Modelfile. `num_predict=4096` (2026-05-31) is
   the generation ceiling **and** the safety net for that dropped
   `presence_penalty`: on some thin JDs qwen ignores `think=false` and reasons
   **in-band**, opening a `reasons[]` JSON string and pouring a monologue into it
   until it exhausts `num_ctx` (~16k tokens ≈ 210s) — that blows past the 240s
   gateway timeout and hangs the whole `scan` (measured: 8000 tokens,
   `done_reason=length`, 28 KB of unterminated JSON). 4096 sits above the largest
   legitimate output (tailor at 700 words ≈ ~2.2k tokens) so it never truncates
   real work, while bounding each generation to ~50s; a pathological JD is
   abandoned in ~100s end-to-end (the cap × `complete_json`'s one invalid-JSON
   retry) — a fast, logged failure instead of the prior 240s-per-attempt hang. It
   stops the *hang*, not the underlying in-band
   reasoning, so the pathological JD still fails to score (truncated JSON is
   invalid); making qwen stop reasoning in-band (schema `maxItems`, a score-only
   `presence_penalty`, or a `done_reason=length` retry) is logged as future work.
   Override per call via `complete_json(options=…)`; the `temperature` kwarg
   always wins.
6. **Honesty enforcement is structural.** The tailor pipeline's
   `_enforce_no_fabrication` rejects any role/employer/dates that diverge from
   `verified.json`, any skill not in `verified.json` (paren-substring tolerated),
   and any "Familiar" skill in a non-Familiar category. Adding a new tailoring
   capability MUST keep these checks green.

   **Deterministic retry on violation (May 2026).** `_enforce_no_fabrication`
   raises `FabricationError` (a `PipelineError` subclass) carrying structured
   `FabricationViolation(kind, detail)` records. `tailor_resume_with_retry`
   in `pipeline/tailor.py` catches that error, builds a kind-specific
   correction hint via `_format_tailor_revision_hint` (mirrors
   `pipeline.cover.write_cover_with_retry`), appends it to the user prompt,
   and re-runs the LLM up to `cfg.pipeline.tailor_retry_attempts` (default
   3) times. After the final failed attempt, the loop re-raises so
   `apply_cmd` surfaces the failure and skips the job — retry is recovery,
   not relaxation, and a fabricating LLM still gets rejected. Use this
   entry point in `apply_cmd`; tests and one-shot tooling can still call
   the legacy `tailor_resume` for a single attempt.

   **Retry temperature (Phase 9).** `_tailor_once` forces `temperature=0`
   when a `revisions` hint is non-empty (i.e. attempts 2+). At the
   frontmatter default (0.3) qwen kept re-sampling the same JD-mirrored
   skill (Targeted Talent JDs that say "Redux required" had qwen producing
   Redux on all 3 attempts despite the corrective hint). At temp=0 the
   model deterministically obeys the "REMOVE 'Redux'" instruction. First
   attempt still uses the frontmatter temperature so legitimate creative
   tailoring isn't punished.
7. **Transferable-skill matching is in the score prompt.** `kb/prompts/score.md`
   defines peer-tech families refreshed for May 2026: frontend (React↔Vue↔
   Svelte↔Angular↔SolidJS↔Preact), meta-frameworks (Next.js↔Remix↔Astro↔
   SvelteKit↔Nuxt↔Qwik), JS/TS runtimes (Node↔Bun↔Deno), edge (Cloudflare
   Workers↔Vercel Edge↔Lambda@Edge↔Deno Deploy), Node servers (Express↔
   Fastify↔Koa↔NestJS↔Hono), ORMs (Prisma↔Drizzle↔Knex↔TypeORM↔Sequelize↔
   Kysely), API patterns (REST↔tRPC; GraphQL stays a gap), relational DBs
   (Postgres↔MySQL↔SQLite↔MariaDB↔CockroachDB), document/KV (MongoDB↔
   DynamoDB↔Firestore↔Redis), vector DBs (Pinecone↔Weaviate↔pgvector↔
   Qdrant↔Chroma↔Milvus), JS test runners (Jest↔Vitest↔Mocha↔Bun test), E2E
   (Playwright↔Cypress↔Puppeteer↔WebdriverIO), cloud (AWS↔GCP↔Azure),
   containers (Docker↔Podman), CI (GitHub Actions↔GitLab CI↔CircleCI↔
   Buildkite↔Jenkins), CMS / e-commerce (Shopify↔BigCommerce↔WooCommerce↔
   Medusa; Contentful↔Strapi↔Sanity↔Ghost↔Payload↔Storyblok), AI SDKs
   (OpenAI↔Anthropic↔Bedrock↔Vertex AI↔Ollama), LLM orchestration
   (LangChain↔LlamaIndex↔Haystack↔DSPy).

   **Auto-decline triggers (recalibrated 2026-05-22, YoE-aware):** The
   score prompt receives `cfg.applicant.years_experience` from the user
   message and drives decisions from that single value:
   - **Years required > YoE + 3** with no transferable bridge declines.
     At 3 YoE, "7+ years required" declines; "5+ years" is borderline
     (score 55–70). At 5 YoE, "9+ years" declines; "7+ years" is
     borderline. Replaces the prior blanket 7+ rule.
   - **Senior-band titles** (Senior / Sr. / Lead / Staff / Principal /
     Architect) decline when YoE < 4. When YoE ≥ 4, treat them as IC
     roles and score in the 60–85 band; auto-decline only when the JD
     body names hard people-management responsibilities (mentoring 4+
     direct reports, owning headcount, performance reviews).
   - **Hard people-management titles** (Manager / Director / Head of /
     VP) always decline, regardless of YoE.
   - **4+ hard gaps** with at least one **Tier-1 ask** ("required", "5+
     years of", "strong production experience with") still declines.
     Vague "nice-to-haves" do not trigger.

   The deterministic `_is_bogus_senior_decline` guard that previously
   nullified Senior/Lead/Staff declines was removed (2026-05-22) — it
   was protecting the *prior* "Senior is fine" calibration and is no
   longer correct. The score prompt now emits these declines
   legitimately, so no override is needed.

   **`pipeline.min_score` defaults to 55** (lowered from 65 in May 2026).
   The 55-59 band is the "stretch, tailor required" zone where a strong
   AI/LLM cover hook can break through — Casey's highest-leverage band
   given his interview-rate situation. Raise back to 65 in config.toml if
   the list gets noisy.

   **Thin-JD confidence cap (2026-05-31 audit fix).** The deterministic
   `must_have_count < 3` carve-out in `pipeline.score` skips the coverage
   clamp for signal-poor JDs (Adzuna's ~500-char snippets yield 1-2 phrases,
   so a 1/2 denominator over-penalizes). It used to pass the **raw** LLM
   score through unbounded — but the model can't penalize gaps it can't see,
   so thin snippets floated to 82-88 and outranked fully-described full-JD
   roles. A 2026-05-31 score audit found the same ZoomInfo *Full Stack
   Engineer* scored **82** from its 500-char Adzuna snippet vs **55** from
   the 7,140-char Greenhouse JD. The carve-out now caps thin postings at
   `cfg.pipeline.thin_jd_score_cap` (default **70**) when
   `len(description) < cfg.pipeline.thin_jd_chars` (default **800** — Adzuna
   snippets run ~500, real ATS JDs 4,000-7,000). The cap **only lowers**, so
   the original "don't drag a 1/1 down to the coverage clamp's 64 floor"
   intent holds for any score ≤ ceiling, and roles stay applyable (> 55)
   without dominating the queue. Long JDs that merely yield <3 must-haves
   (e.g. manual `apply --url` full-JD fetches) are exempt via the length
   gate. A code-only change to `score.py` does NOT bump `prompt_hash`, so the
   cap applies to newly-scored jobs — re-score the backlog to correct an
   existing queue. Both thresholds are config knobs; tune `thin_jd_score_cap`
   up if the thin-JD band looks under-ranked.

   **Familiar-only-fit cap (Phase 10.2).** When every matched must-have
   resolves into `verified.skills_familiar` (Java, Spring Boot, MCP
   Servers, Agile/Scrum, Headless Architecture, Figma) and NOT into any
   Core bucket, the deterministic post-filter in `pipeline.score`
   (`_all_matched_are_familiar`) caps the score at 54 and sets
   `decline_reason = "role's matched skills are all Familiar (academic/
   light use only)..."`. Without this cap, qwen's transferable-skill
   rubric over-credited a Java Developer role at score=78 and the tailor
   shipped a resume containing ONLY a Familiar category — actively
   misrepresenting Casey. Word-boundary matching is used so "Java" does
   not match the "JavaScript" substring (which would incorrectly resolve
   Java into skills_core and bypass the cap).

   **`skills_projects` bucket (Phase PB1, 2026-06-01).** A verified skill
   bucket for skills demonstrated in Casey's shipped personal projects
   (FastAPI, Redis, Claude API, Docker Compose, JSON-LD, agentic
   architecture). It is parsed from a `Project Stack:` labeled line in the
   `Resume.docx` TECHNICAL SKILLS section, reusing the existing
   labeled-skills-line mechanism (one entry in `parse_docx.skill_buckets`).
   Honesty semantics: project-demonstrated and honest to claim, Core-grade,
   distinct from `skills_familiar` (academic / light use) and the professional
   Core buckets (paid client work). It is treated as a Core bucket by
   `_all_matched_are_familiar`, so a JD whose only match is a project skill is
   NOT Familiar-capped. PB1 wires it for scoring only. Rendering project skills
   on the tailored resume (PB2) and parsing a `PROJECTS` narrative section
   (PB3) are later phases. Until PB3 lands, do NOT run `convert-resume` against
   the projects-augmented `Resume.docx`: the parser does not yet know the
   `PROJECTS` section and would mis-file those lines into `education`.

   **React umbrella (2026-05-28).** The verified Core skill is
   `React (Redux, React Native)` — one entry covering the React ecosystem.
   `_enforce_no_fabrication`'s identity-subset check accepts `React`, `Redux`,
   and `React Native` as surface forms of it (their identity tokens are a
   subset), so a Redux-required JD tailors cleanly without a standalone Redux
   item. Prompt/policy render it as plain "React" by default; Redux/React
   Native surface as explicit items only when the JD names them (Core-grade,
   never Familiar). (A `skills_projects` middle tier existed briefly in May
   2026 but was removed — Casey's only project-tier skill, Astro, moved to
   Familiar and React Native folded into this umbrella.)

## Post-generation audit rules

After `tailor_resume` + `write_cover`, `pipeline.audit.audit()` runs before
.docx render. It is **deterministic and LLM-free** — do not add an Ollama call
to it without explicit discussion.

1. **Keyword coverage** — JD must-haves (from the score result) must appear in
   the tailored resume at ≥70 % (2026 ATS guideline). Verdict `revise` if below
   the soft `MIN_KEYWORD_COVERAGE_PCT` threshold; verdict `block` if below the
   hard `HARD_COVERAGE_FLOOR_PCT` floor (default 50). Sub-50% coverage means the
   keyword screen will toss the resume before any human sees it, so the apply
   loop skips the job rather than silently shipping noise. Added 2026-05-27
   after the OCR/Tesseract case (manual:db425a17, score=72 but audit=0%) and the
   ElectronJS/WebSocket case (manual:4bcf846a, 43%) both rendered as `revise`
   and got submitted.
   When `scores.reasons` is empty (qwen3.5:9b often ships empty arrays despite
   the schema requiring them), `audit._extract_must_haves_from_jd` runs as a
   deterministic fallback — intersect verified skills with `job_title ∪
   job_description`. Title is part of the source because Adzuna ships ~500-char
   description snippets where canonical tech names ("Java", "React") often
   only survive in the title. Adding new tailoring capabilities must not
   break this fallback path.

   **Peer-family broadening (May 2026)** — when the JD is short (< 800 chars,
   signaling Adzuna) AND the score's matched-must-haves is empty, the
   fallback also counts a verified skill as a must-have when the JD names
   one of its peers per `pipeline._keywords.PEER_FAMILIES`. Example: JD
   names "Vue", verified has "React" → React surfaces as an inferred
   must-have; the tailor's JD-surface-form rule renders the JD's exact
   token ("Vue") in the output where appropriate. Long JDs skip this
   broadening to avoid false positives. `PEER_FAMILIES` is shared between
   `kb/prompts/score.md` (transferable matching) and audit fallback
   (must-have extraction) — single source of truth in `pipeline._keywords`.

   **Peer-broadening dedupe (Phase 10.1).** When a verified skill is
   already matched directly via `phrase_present`, its peer-family
   siblings are NOT added as inferred must-haves. Example: Casey has
   both AWS and Azure verified; JD only names AWS. AWS matches directly
   → cloud_provider family is "covered" → Azure is not inferred. Without
   this dedupe, the tailor (correctly omitting Azure since the JD doesn't
   ask) saw audit mark Azure as a missing must-have and `keyword_coverage`
   drop to 80%. The `peer_family_of` helper exposed by
   `pipeline._keywords` powers this check; same-family verified skills
   that ALL went into `gaps` rather than `matched` still surface
   normally.

   **Resume↔cover alignment check (May 2026)** — `audit._alignment_flags`
   scans both artifacts for project anchors mined from `verified.json`
   work history (Atelier Dacko / vintage gaming / HubSpot / Ollama). When
   the cover's middle paragraph anchors on a different verified project
   than the resume's first role's first bullet, an `alignment_flags`
   entry fires and the verdict is `revise` (not block). The bare term
   "Shopify" is intentionally NOT an anchor because both Atelier Dacko
   and Vintage Gaming are Shopify projects — distinct anchors must
   identify exactly one verified project.

   **End-of-loop summary (May 2026)** — `apply --top N` and `apply --best`
   loops emit a one-line summary after the last job: `N drafted, M with
   revise warnings, K blocked` plus a histogram of top warning topics
   (fabrication / cover-violation / coverage / alignment) so the user
   sees the aggregate pattern across the batch.
2. **Cover-letter validator** (`pipeline.cover_validate`) — enforces banned
   phrases (substring tier + structural `_DEFENSIVE_PATTERNS` regex tier for
   defensive gap-volunteering like "rather than X", "the model transfers"),
   word count, paragraph count, company name in lead (tokenized: splits on
   whitespace+punctuation, drops corporate suffixes like `Inc`/`Technologies`
   and TLD fragments like `.io`/`.ai` via `_COMPANY_STOPWORDS`, accepts any
   distinctive remaining token), no unverified numbers (digits embedded in
   alphanumeric tokens like ES6 are exempt), no closing diploma re-recap.

   **May 2026 refresh:**
   - `BANNED_PHRASES` trimmed — `"track record"` and `"production-grade"`
     dropped (too generic; fired on legitimate sentences).
   - `_FABRICATION_WATCHLIST` refreshed for 2026 JS/TS + LLM stack: Bun, Hono,
     tRPC, Prisma, Drizzle, Astro, SvelteKit, Qwik, LangChain, LlamaIndex,
     Haystack, Pinecone, Weaviate, Qdrant, Chroma, Milvus, Bedrock, Vertex AI.
     Python removed (now Core in `verified.json` after Phase 1).
   - `_NEGATION_PRECEDES_RE` extended with `however`, `but i don't`,
     `though i haven't` so legitimate disclaiming context suppresses the
     watchlist (a cover saying "However, I haven't worked with Kubernetes"
     no longer fires the Kubernetes fabrication flag).
   - `_DEFENSIVE_PATTERNS` extended with the `'X concepts in/of/with'`
     regex (Phase 8) — catches "I have also worked with GraphQL concepts
     in my data layer" framing that slipped past the earlier
     rather-than / coming-from patterns. Triggered by any of
     `worked with`, `experience in/with`, `exposure to`, `familiarity with`,
     `familiar with`, `knowledge of`, `understanding of` followed by a
     tech token + `concepts`. Legitimate uses ("The migration taught me
     concepts that…") pass because they don't open with the watch-listed
     verb phrase.
   - **Overreach patterns (2026-05-27).** New `_OVERREACH_PATTERNS` tuple
     catches *framing-level* capability claims that aren't single tech
     tokens, so they slipped past `_FABRICATION_WATCHLIST`: `live data
     streams`, `real-time streaming/processing`, `websockets`,
     `event-driven architecture`, `streaming pipelines`, `distributed
     systems`, `high-throughput`. Same suppression structure as the
     fabrication watchlist — match in body, suppress if the phrase
     appears in `_verified_skill_blob`, suppress if every occurrence is
     in a `_NEGATION_PRECEDES_RE` context. Surface text:
     `unverified capability claim: '<label>'` (rule_id
     `unverified_capability` for `analyze validators`). Added after cover
     `manual:4bcf846a` opened with "...applications in TypeScript,
     Node.js, and Express that handle live data streams and complex user
     workflows" — Casey has zero stream/real-time work verified, but the
     existing watchlist only caught named libs, not capability framing.
   - **Digit-cluster boundary fix (Phase 9).** `_DIGIT_CLUSTER_RE` now
     excludes `_` on both sides of the cluster so underscore-joined tech
     tokens like `q5_0` (KV-cache quantization name, verified in
     `skills_ai`) stay atomic. Without this, `q5_0` fragments into
     `q5` + `_` + `0`, the trailing `0` gets flagged as an unverified
     number, and the cover ships with a spurious `revise` verdict.
     Legitimate standalone `0` (e.g. "0 regressions") still flags.
   Two preprocess steps run before matching to defang model quirks:
   - **Apostrophe normalization** — `_normalize()` collapses curly/smart
     apostrophes (U+2019 and variants) to ASCII `'` before banned-phrase /
     opener / closing / salutation / company-name checks. Without this,
     qwen's typographic output (e.g. `team's goals`) bypasses the substring
     tier whose constants use ASCII `'`.
   - **Time-of-day stripping** — `_TIME_OF_DAY_RE` removes clock references
     (`11:00 AM`, `9 a.m.`, `5pm`, bare `12:30`) before the unverified-numbers
     digit-cluster pass. The cluster regex breaks on `:`, so without this
     stripping a JD stand-up reference (`11:00`) flagged as two fabricated
     numbers (`11`, `00`).
   Verdict `revise` on violations.
3. **Fabrication re-check** — `_enforce_no_fabrication` runs again on the
   tailored resume post-decode. Verdict `block` on any failure.
4. **Verdicts:** `block` → the apply loop skips this job and logs the reason;
   `revise` → docs are still rendered but warnings are printed to stderr and
   written to `data/applications/<id>/audit.json`; `ship` → clean pass.
5. **`config calibrate`** (hidden subcommand) prints interview-rate per score
   band from `applications`. Use after ≥20 applications to tune `pipeline.min_score`.
6. **`pipeline.min_score`** is now set in `config.toml` under `[pipeline]`
   (default **55** as of May 2026, lowered from 65). The `--min-score` CLI
   flag overrides it per run. See §"LLM call rules" item 7 for rationale.
7. **One-page guarantee** — `tailor._shrink_to_one_page` enforces a hard
   single-page output via `render_docx.fits_one_page` (48-line budget,
   wrap-aware). The shrink ladder runs in this fixed order — adding new
   content-density features must respect it:
   1. Trim summary down to ≥3 sentences.
   2. Trim Familiar skills down to ≥4 items.
   3. Drop the last bullet of the role with the highest current line-cost
      (each role keeps ≥1 bullet — the JD-relevant lead). **May 2026 guard
      in `_try_drop_weakest_bullet`:** while any older role still has spare
      bullets (> 1), the role whose `dates` contains "Present" is skipped
      — the current contract is the strongest JD-recent signal. Once all
      older roles are at one bullet, the Present role becomes eligible.
   4. Drop the coursework block.
   If the resume still overflows after step 4, the tailor raises
   `PipelineError` — caller surfaces the failure and the user is expected to
   tighten verified.json bullets at the .docx source.

8. **JD surface-form discipline** (`kb/prompts/tailor.md` rule 9). Tailored
   bullets and skill items MUST use the JD's exact substring form for tech
   keywords when that form maps to a verified fact (e.g. JD "Postgres" →
   "Postgres", not "PostgreSQL"; JD "JS" → "JS"; JD "GH Actions" → "GH
   Actions"). AI-screeners score on substring presence, not synonym mapping.
   `_enforce_no_fabrication` (`pipeline/tailor.py`) accepts these surface
   variants via the `_ANNOTATION_TOKENS` allowlist while still rejecting
   superset claims like "React Native" against verified "React".

9. **Lead-category size cap** (`pipeline/tailor._cap_lead_category_size`).
   The tailor prompt rule 10 caps the first skills category at 6-10 items,
   but live runs showed qwen3.5:9b obeyed that only ~38% of the time
   (5/8 outputs had 11-12 items in the lead). Phase 9 adds deterministic
   enforcement: after `_complete_familiar_bucket` and before
   `_shrink_to_one_page`, any items past index 10 in the lead category
   are prepended to the next non-Familiar category (preserving JD-priority
   ordering) — or, if the only secondary is Familiar, moved to a new
   "Additional" bucket inserted before Familiar. Verified skills are
   never dropped, just demoted to non-lead position.

10. **JD-required-skill backfill** (`pipeline/tailor._ensure_jd_required_skills`,
    2026-05-31). `_tailor_once` never sees the JD must-haves, so when the LLM
    reorganizes verified skills into JD-relevant categories it sometimes drops
    infra/cloud/tooling skills that don't fit them — even when the JD requires
    them. Observed: the shyftlabs JD required Git/AWS/Azure (all in
    `verified.skills_data_devops`), but the tailor folded that bucket into a
    "Backend & APIs" category and dropped them, sinking audit keyword coverage to
    62%. This deterministic post-processor (after `_complete_familiar_bucket`,
    before `_cap_lead_category_size` / `_shrink_to_one_page`) re-adds any verified
    non-Familiar skill the JD names (`phrase_present` vs title+description — the
    same primitive `audit.keyword_coverage` uses, with paren-stripped cores so
    "React" satisfies "React (Redux, Native)") that the flattened tailored resume
    omits, placing it in the category with the most same-bucket siblings (else the
    last non-Familiar category, else a new "Additional" bucket). Honest by
    construction — only ever re-adds skills already in `verified.json`. Familiar
    is left to `_complete_familiar_bucket`. The fix recovered the shyftlabs
    artifact from 62% to 100% coverage. (Adding cloud LLM/runtime path is still
    forbidden; this is a pure deterministic post-process.)

## Testing

- `pytest -q` is the gate. No live HTTP or Ollama calls in the test suite.
- Tests live under `tests/`:
  - **Pure helpers** (`_filter`, `parse_docx`, `_parse_picks`, `render_docx` page-fit, db upserts, tailor invariants) — unit-tested directly.
  - **Pipeline integration** (real Ollama) — manual; not in CI. Run by hand after prompt changes.
  - **Browser autofill** — manual; not in CI. Run via `apply --no-browser` first to verify docs, then re-run with the browser.
- When adding an ingest adapter, capture a sample API response under
  `tests/fixtures/<source>.json` and unit-test the parser against it (no
  network).

## What Claude Code should NOT do

- Do not add cloud LLM provider code (OpenAI, Anthropic, etc.) to the runtime path. Building tools using cloud is fine; runtime is local-only.
- Do not introduce an ORM (SQLAlchemy, Tortoise, etc.).
- Do not add a web framework. CLI only for now.
- Do not write scrapers for LinkedIn, Indeed, Glassdoor, or any site that prohibits it in ToS. If asked, refuse and reference this file.
- Do not bypass the gateway for LLM calls.
- Do not commit anything in `data/`, `~/.config/jobhunt/`, or files matching `*.secret.*`.
- Do not auto-submit applications. Ever.

## When stuck

If a request is ambiguous, prefer the smaller, testable interpretation. Surface the ambiguity in your output as a "Decisions made" section so the user can correct in the next pass. Never widen scope silently — adding a new ingest source, a new ATS handler, or a new prompt is a discrete change with its own review.
