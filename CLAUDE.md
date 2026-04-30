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

- Arch Linux, Ryzen 9 5900, 32GB DDR4, RTX 3080 (10GB VRAM)
- Ollama at `http://localhost:11434`
- Default models: `qwen3:14b` (Q4_K_M) for generation, `qwen3:8b` (Q5_K_M) for classification/JSON, `nomic-embed-text` for embeddings
- One model hot in VRAM at a time. Respect this when designing flows.

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
is `job-seeker`.

```
src/jobhunt/
├── cli.py                     # Typer app, subcommand wiring only
├── commands/
│   ├── convert_resume_cmd.py  # P1
│   ├── scan_cmd.py            # P2: ingest + score
│   ├── apply_cmd.py           # P3+P4: tailor + cover + autofill
│   ├── list_cmd.py            # P5: pipeline view + weekly rollup
│   ├── db_cmd.py              # hidden internal
│   └── config_cmd.py          # hidden internal
├── resume/
│   ├── parse_docx.py          # baseline .docx → verified.json + kb/profile/*.md
│   └── render_docx.py         # tailored markdown → ATS-safe .docx
├── ingest/                    # one file per source
│   ├── _filter.py             # GTA allowlist + Remote-Canada heuristic
│   ├── greenhouse.py
│   ├── lever.py
│   ├── ashby.py
│   └── adzuna_ca.py
├── gateway/                   # Ollama client + prompt loader
│   ├── client.py              # complete_json (POST /api/chat with format=schema)
│   └── prompts.py             # frontmatter-aware markdown prompt loader
├── pipeline/                  # score, tailor, cover
│   ├── score.py
│   ├── tailor.py              # enforces no-fabrication invariants
│   └── cover.py
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
job-seeker convert-resume       # parse baseline .docx → kb/profile/
job-seeker scan                 # ingest GTA jobs + score
job-seeker apply <job-id>       # tailor + cover + autofill (you submit)
job-seeker apply --top N        # auto-pick N best-fit unapplied (1..10)
job-seeker apply --best         # interactive picker over top 10
job-seeker list [--week N]      # pipeline view + weekly rollup
```

Subcommand groups map to modules in `commands/`. Keep `cli.py` to wiring only.

## Ingestion rules — non-negotiable

1. **Public APIs only.** Greenhouse `boards-api`, Lever `api.lever.co/v0`, Ashby posting API, Adzuna CA (with API key), Job Bank Canada RSS, generic RSS.
2. **GTA scope.** Filter by GTA city allowlist (Toronto, Mississauga, Brampton, Hamilton, Oakville, Markham, Vaughan, Burlington, Oshawa, Richmond Hill, Pickering, Ajax, Whitby, Milton) **plus Remote-Canada** postings. Adzuna uses `where=Toronto&distance=100&country=ca`. Drop everything else.
3. **No LinkedIn, no Indeed, no Glassdoor scraping**, ever. Even if the user asks. Push back and explain.
4. **Respect `robots.txt`** for any non-API HTTP fetch. Use `protego`.
5. **Rate limits:** 1 req/sec/host default. Exponential backoff on 429/5xx.
6. **User-Agent:** identifies the tool and provides a contact, e.g. `job-seeker/0.1 (+personal-use; caseyhsu@proton.me)`.
7. **Cache** raw responses to `data/cache/` with a TTL; don't re-hit APIs needlessly during dev.

## Browser automation rules — non-negotiable

1. **Never click a submit button.** Fill fields, then hand off to the human. The user is in the loop on every application.
2. **Never auto-create accounts** on employer sites. If signup is required, exit and tell the user.
3. **Log a field-fill plan** to `data/applications/<job-id>/fill-plan.json` before executing it, for auditability.
4. **Run headed by default.** Headless only if `--headless` flag and only for dry-runs.
5. **No stored employer credentials.** If a site requires login, the user logs in manually each time.

## LLM call rules

1. **Every structured call uses a JSON schema.** `gateway.client.complete_json(schema=...)` posts to Ollama `/api/chat` with `format: <schema>`. No free-form JSON parsing.
2. **Truncate inputs** to fit `num_ctx`. The score/tailor pipelines truncate description to 6000 chars and policy to 4000 — see `pipeline.score.truncate`.
3. **Default temperatures** are set in prompt frontmatter: scoring 0.0, tailoring 0.3, cover letters 0.5.
4. **Honesty enforcement is structural.** The tailor pipeline's
   `_enforce_no_fabrication` rejects any role/employer/dates that diverge from
   `verified.json`, any skill not in `verified.json` (paren-substring tolerated),
   and any "Familiar" skill in a non-Familiar category. Adding a new tailoring
   capability MUST keep these checks green.

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
