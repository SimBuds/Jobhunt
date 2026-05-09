# CLAUDE.md

**Source of truth for agents working in this repo.** Auto-loaded by Claude Code on every session. Defines the architecture, conventions, and non-negotiable guardrails for `jobhunt`.

If anything in `PLAN.md` contradicts this file, **this file wins** — open a PR to reconcile rather than working around it.

- `PLAN.md` — design rationale and reference for implementation choices.
- `README.md` — end-user install and usage. Don't put dev/agent guidance there.
- `Resume_Tailoring_Instructions.md` — non-negotiable rules for tailoring (no fabrication, ATS-safe formatting, auto-decline triggers). Mirrored at `kb/policies/tailoring-rules.md` for prompt injection.

---

## What this project is

A local-first CLI tool for personal job search automation. Pulls jobs from public ATS APIs, runs fit-scoring and document tailoring against the user's profile using local Ollama models, and assists with form autofill via Playwright (human submits, never the bot).

## Hardware context

- Arch Linux, Ryzen 9 5900, 32GB DDR4, RTX 3080 (10 GB VRAM total). Arch idles around 1.5 GB on the GPU, so `OLLAMA_GPU_OVERHEAD` is intentionally **not** set — the full 10 GB is available to Ollama and qwen3.5:9b lands at ~9.1 GB resident with comfortable headroom.
- Ollama at `http://localhost:11434`
- Default model: `qwen3.5:9b` for all task slots (score, tailor, cover) — single hot model at `num_ctx=6144` with `keep_alive="30m"` and reasoning (`think`) disabled at the gateway; `nomic-embed-text` reserved for future embeddings. QA is deliberately deterministic (see `pipeline.audit`) — no LLM QA slot.
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
│   ├── list_cmd.py            # P5: pipeline view + weekly rollup
│   ├── db_cmd.py              # hidden internal
│   └── config_cmd.py          # hidden internal
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
│   ├── job_bank_ca.py         # Government of Canada Job Bank RSS
│   └── rss_generic.py         # generic employer career RSS/Atom feeds
├── gateway/                   # Ollama client + prompt loader
│   ├── client.py              # complete_json (POST /api/chat with format=schema)
│   └── prompts.py             # frontmatter-aware markdown prompt loader
├── pipeline/                  # score, tailor, cover, audit, cover_validate
│   ├── score.py
│   ├── tailor.py              # enforces no-fabrication invariants
│   ├── cover.py
│   ├── cover_validate.py      # deterministic cover-letter validator (banned phrases, etc.)
│   └── audit.py               # post-generation audit: keyword coverage + verdict
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

User-facing surface is **four** commands. `db` and `config` are hidden internals.

```
jobhunt convert-resume       # parse baseline .docx → kb/profile/
jobhunt scan                 # ingest GTA jobs + score
jobhunt apply <job-id>       # tailor + cover + autofill (you submit)
jobhunt apply --top N        # auto-pick N best-fit unapplied (1..10)
jobhunt apply --best         # interactive picker over top 10
jobhunt list [--week N]      # pipeline view + weekly rollup
```

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

1. **Public APIs only.** Greenhouse `boards-api`, Lever `api.lever.co/v0`, Ashby posting API, Adzuna CA (with API key), SmartRecruiters public Posting API (`api.smartrecruiters.com/v1/companies/{slug}/postings`, no key), Job Bank Canada RSS, generic RSS.
2. **GTA scope.** Filter by GTA city allowlist (Toronto, Mississauga, Brampton, Hamilton, Oakville, Markham, Vaughan, Burlington, Oshawa, Richmond Hill, Pickering, Ajax, Whitby, Milton) **plus Remote-Canada** postings. Adzuna uses `where=Toronto&distance=100&country=ca`. Drop everything else.
3. **No LinkedIn, no Indeed, no Glassdoor scraping**, ever. Even if the user asks. Push back and explain.
4. **Respect `robots.txt`** for any non-API HTTP fetch. Use `protego`.
5. **Rate limits:** 1 req/sec/host default. Exponential backoff on 429/5xx.
6. **User-Agent:** identifies the tool and provides a contact, e.g. `jobhunt/0.1 (+personal-use; caseyhsu@proton.me)`.
7. **Cache** raw responses to `data/cache/` with a TTL; don't re-hit APIs needlessly during dev.

## Browser automation rules — non-negotiable

1. **Never click a submit button.** Fill fields, then hand off to the human. The user is in the loop on every application.
2. **Never auto-create accounts** on employer sites. If signup is required, exit and tell the user.
3. **Log a field-fill plan** to `data/applications/<job-id>/fill-plan.json` before executing it, for auditability.
4. **Run headed by default.** Headless only if `--headless` flag and only for dry-runs.
5. **No stored employer credentials.** If a site requires login, the user logs in manually each time.

## LLM call rules

1. **Every structured call uses a JSON schema.** `gateway.client.complete_json(schema=...)` posts to Ollama `/api/chat` with `format: <schema>`. No free-form JSON parsing.
2. **Reasoning disabled.** The gateway sends `"think": false` so qwen3.5's
   reasoning trace doesn't blow past the timeout on structured calls. Quality
   is held by the deterministic post-processing layers (score clamp, cover
   validator + retry, audit), not by reasoning tokens. If a future task slot
   needs thinking, plumb it through as a per-call kwarg — don't flip the
   default.
3. **Keep-alive + warm-up.** `keep_alive="30m"` in the payload so the model
   stays resident across a scan. `scan_cmd._warm_model()` fires a tiny chat
   before the scoring loop so the first real call doesn't pay cold-load on
   top of the 180 s gateway timeout.
4. **Truncate inputs** to fit `num_ctx` (default 6144). The score/tailor pipelines truncate description to 6000 chars and policy to 4000 — see `pipeline.score.truncate`.
5. **Default temperatures** are set in prompt frontmatter: scoring 0.0, tailoring 0.3, cover letters 0.7 (the cover prompt is tuned around the wider creative latitude — don't drop it back to 0.5 without re-tuning the anti-pattern rules).
6. **Honesty enforcement is structural.** The tailor pipeline's
   `_enforce_no_fabrication` rejects any role/employer/dates that diverge from
   `verified.json`, any skill not in `verified.json` (paren-substring tolerated),
   and any "Familiar" skill in a non-Familiar category. Adding a new tailoring
   capability MUST keep these checks green.
7. **Transferable-skill matching is in the score prompt.** `kb/prompts/score.md`
   defines peer-tech families (React↔Vue↔Svelte, Express↔Fastify↔Koa,
   Postgres↔MySQL↔SQLite, AWS↔GCP↔Azure, etc.) so closely-related experience
   counts as matched, not as gaps. Auto-decline triggers are conservative:
   "Senior" alone is **not** a decline; only Lead/Principal/Architect/Staff
   *with* stated leadership responsibilities, 5+ year hard requirements, or
   non-IC titles. The gap threshold is 4+ hard gaps.

## Post-generation audit rules

After `tailor_resume` + `write_cover`, `pipeline.audit.audit()` runs before
.docx render. It is **deterministic and LLM-free** — do not add an Ollama call
to it without explicit discussion.

1. **Keyword coverage** — JD must-haves (from the score result) must appear in
   the tailored resume at ≥70 % (2026 ATS guideline). Verdict `revise` if below.
   When `scores.reasons` is empty (qwen3.5:9b often ships empty arrays despite
   the schema requiring them), `audit._extract_must_haves_from_jd` runs as a
   deterministic fallback — intersect verified skills with the JD description
   and use those as must-haves. Adding new tailoring capabilities must not
   break this fallback path.
2. **Cover-letter validator** (`pipeline.cover_validate`) — enforces banned
   phrases (substring tier + structural `_DEFENSIVE_PATTERNS` regex tier for
   defensive gap-volunteering like "rather than X", "the model transfers"),
   word count, paragraph count, company name in lead (tokenized: splits on
   whitespace+punctuation, drops corporate suffixes like `Inc`/`Technologies`
   and TLD fragments like `.io`/`.ai` via `_COMPANY_STOPWORDS`, accepts any
   distinctive remaining token), no unverified numbers (digits embedded in
   alphanumeric tokens like ES6 are exempt), no closing diploma re-recap.
   Verdict `revise` on violations.
3. **Fabrication re-check** — `_enforce_no_fabrication` runs again on the
   tailored resume post-decode. Verdict `block` on any failure.
4. **Verdicts:** `block` → the apply loop skips this job and logs the reason;
   `revise` → docs are still rendered but warnings are printed to stderr and
   written to `data/applications/<id>/audit.json`; `ship` → clean pass.
5. **`config calibrate`** (hidden subcommand) prints interview-rate per score
   band from `applications`. Use after ≥20 applications to tune `pipeline.min_score`.
6. **`pipeline.min_score`** is now set in `config.toml` under `[pipeline]`
   (default 65). The `--min-score` CLI flag overrides it per run.

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
