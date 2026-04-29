# AGENTS.md

Phase-by-phase prompts for Claude Code. Paste one phase at a time into a fresh Claude Code session, let it execute, review, then move to the next. Each phase has explicit exit criteria — don't advance until they're green.

`PLAN.md` has the strategic context. `CLAUDE.md` has the conventions Claude Code auto-loads. This file has the *operational instructions* per phase.

---

## How to use this file

1. Open a Claude Code session at the repo root.
2. Confirm `CLAUDE.md` and `PLAN.md` are visible to it (`/init` if needed).
3. Paste the **prompt block** for the current phase.
4. When Claude Code says it's done, run the **verification commands** yourself.
5. If verification passes, commit. If not, paste the failure output back in and iterate.
6. Move to next phase.

Treat phases as atomic. Don't let scope from phase N+1 leak into N.

---

## Phase 0 — Foundations

> Goal: bootstrap a working repo skeleton with deps, config, DB schema, and `kb/` placeholders.

### Prompt for Claude Code

```
Implement Phase 0 from PLAN.md. Read CLAUDE.md first and follow every convention in it.

Tasks:

1. Initialize the project with `uv init` (Python 3.12+). Set up pyproject.toml with these deps: typer, httpx, pydantic>=2, structlog, tomli-w. Dev deps: pytest, pytest-asyncio, ruff, mypy.

2. Create the directory structure exactly as specified in CLAUDE.md under "Project structure".

3. Implement `src/jobhunt/config.py`:
   - Pydantic model `Config` with sections: `paths`, `ingest`, `gateway`, `pipeline`, `browser`
   - Loader function that reads `~/.config/jobhunt/config.toml`, merges env vars (prefix JOBHUNT_), validates
   - On first run, write a sensible default config.toml and tell the user where it lives
   - Include a `Config.example_toml()` classmethod that returns the default as a string

4. Implement `src/jobhunt/db.py`:
   - SQLite connection helper with WAL mode, foreign keys on
   - Migration runner that reads numbered SQL files from `migrations/`
   - Write `migrations/0001_init.sql` creating tables: `jobs`, `companies`, `scores`, `applications`, `migrations`. Schema for `jobs` should include: id (uuid), source, external_id, company, title, location, remote_type, description, url, posted_at, ingested_at, raw_json. Add appropriate indexes and a UNIQUE constraint on (source, external_id).

5. Implement `src/jobhunt/cli.py` with Typer:
   - Top-level app with subcommand groups: ingest, list, show, score, tailor, cover, review, apply, status, model, eval, kb, config, db
   - Stub each subcommand with a `NotImplementedError("phase X")` message that tells the user which phase will implement it
   - `db migrate` and `db init` should actually work
   - `config show` should actually work and print the resolved config

6. Create `kb/` with:
   - `kb/profile/resume.md` — a template with section headers (Contact, Summary, Experience, Skills, Education) and "TODO: fill in" markers
   - `kb/profile/work-history.md` — template with one example entry showing the expected format (company, role, dates, bullet wins)
   - `kb/profile/skills.md` — template with categories (Languages, Frameworks, Tools, Domains)
   - `kb/profile/stories.md` — template with one STAR-format example
   - `kb/profile/voice.md` — instructions for the user to paste 2-3 writing samples
   - `kb/prompts/.gitkeep`, `kb/schemas/.gitkeep`, `kb/examples/.gitkeep`
   - `kb/README.md` explaining what each subdirectory is for and the editing workflow

7. `.gitignore`: data/, .venv/, __pycache__/, *.pyc, .mypy_cache/, .ruff_cache/, .env, *.secret.*

8. `README.md` with: one-paragraph description, install steps (uv sync, ollama pull qwen3:14b qwen3:8b nomic-embed-text), first-run quickstart pointing at config show, db init.

9. `tests/test_config.py` and `tests/test_db.py` covering: default config loads, env vars override, migrations run idempotently.

10. Run `uv run pytest` and `uv run mypy src/` and `uv run ruff check src/`. Fix anything that's not green.

End with a "Decisions made" section in your final response listing any choices you made that weren't fully specified.
```

### Verification

```bash
uv run pytest                          # all green
uv run mypy src/                       # no errors
uv run ruff check src/                 # no issues
uv run jobhunt --help                  # shows all subcommand groups
uv run jobhunt config show             # prints resolved config
uv run jobhunt db init                 # creates data/jobhunt.db
sqlite3 data/jobhunt.db ".schema jobs" # shows the jobs table
```

### Exit criteria

All verification commands succeed. `kb/` skeleton exists. `.gitignore` excludes `data/`. Commit as `feat: phase 0 foundations`.

---

## Phase 1 — Ingest

> Goal: pull jobs from Greenhouse, Lever, Ashby, USAJobs, and Adzuna into SQLite.

### Prompt for Claude Code

```
Implement Phase 1 from PLAN.md. Re-read CLAUDE.md, especially the "Ingestion rules — non-negotiable" section.

Tasks:

1. Create `src/jobhunt/ingest/base.py` with:
   - `IngestAdapter` Protocol: async `fetch() -> AsyncIterator[RawJob]` and `name: str`
   - `RawJob` Pydantic model with the fields needed to populate the `jobs` table
   - A `normalize(raw: RawJob) -> Job` function that handles common transformations (strip HTML, normalize remote_type, parse posted_at)

2. Implement adapters — one file each, all subclass IngestAdapter:

   - `greenhouse.py`: `GreenhouseAdapter(company_slug)` hitting `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`. Page through if needed.

   - `lever.py`: `LeverAdapter(company_slug)` hitting `https://api.lever.co/v0/postings/{slug}?mode=json`.

   - `ashby.py`: `AshbyAdapter(company_slug)` hitting `https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`.

   - `usajobs.py`: `USAJobsAdapter(keywords, location)` hitting `https://data.usajobs.gov/api/search`. Requires `User-Agent` header set to user email and `Authorization-Key` from secrets. Read both from config.

   - `adzuna.py`: `AdzunaAdapter(country, what, where)` hitting `https://api.adzuna.com/v1/api/jobs/{country}/search/{page}`. Requires `app_id` and `app_key` from secrets. Page through results.

   - `rss.py`: `RSSAdapter(url)` for arbitrary RSS feeds. Use `feedparser`.

3. Common HTTP layer in `src/jobhunt/ingest/http.py`:
   - Single `httpx.AsyncClient` with the User-Agent from CLAUDE.md
   - `aiolimiter` for 1 req/sec per host
   - Retry-with-backoff on 429/5xx (use `tenacity` with reasonable defaults)
   - Disk cache to `data/cache/<host>/<sha1(url)>.json` with 6-hour TTL, bypassed by `--fresh` flag

4. Persistence in `src/jobhunt/ingest/store.py`:
   - `upsert_job(conn, job: Job)` using INSERT ... ON CONFLICT(source, external_id) DO UPDATE
   - Track ingest run stats: total seen, new, updated, errors → return as a small dataclass

5. Configuration in `~/.config/jobhunt/config.toml` under `[ingest]`:
   - List of greenhouse/lever/ashby company slugs
   - USAJobs query params
   - Adzuna query params
   - RSS feed URLs
   - Per-source `enabled` flag

6. Wire commands:
   - `jobhunt ingest add <source> <slug-or-url>` — appends to config.toml after validation
   - `jobhunt ingest list` — shows configured sources
   - `jobhunt ingest run [--source NAME] [--fresh]` — runs all (or one) adapters, upserts jobs, prints stats table
   - `jobhunt list [--source X] [--since DAYS] [--limit N]` — query the DB

7. Tests:
   - For each adapter, a unit test using a recorded fixture in `tests/fixtures/<source>/sample.json`. Mock httpx with `respx`.
   - One `@pytest.mark.integration` test per adapter that hits the real API, skipped by default. Document how to run them in tests/README.md.
   - `tests/test_store.py` for upsert idempotency.

8. Add a `jobhunt ingest doctor` command that checks: secrets present, API keys valid (one cheap call each), rate limits headroom.

End with the "Decisions made" section. Confirm: no scrapers added, no LinkedIn/Indeed code anywhere, robots.txt respected for any RSS that's actually a webpage.
```

### Verification

```bash
# Configure at least one of each
uv run jobhunt ingest add greenhouse stripe
uv run jobhunt ingest add lever netflix
uv run jobhunt ingest add ashby ramp
uv run jobhunt ingest doctor                    # all green or skipped-with-reason
uv run jobhunt ingest run                        # populates DB
uv run jobhunt list --limit 10                   # shows recent jobs
sqlite3 data/jobhunt.db "SELECT source, COUNT(*) FROM jobs GROUP BY source;"
```

### Exit criteria

≥100 jobs across ≥2 source types. Re-running ingest is a no-op (no duplicates). Cache is hit on second run within TTL. All adapter unit tests green. Commit.

---

## Phase 2 — Gateway

> Goal: a single OpenAI-compatible interface to local models with task-based routing, prompt composition, and an eval harness.

### Prompt for Claude Code

```
Implement Phase 2 from PLAN.md. Reread CLAUDE.md "LLM call rules".

Tasks:

1. `src/jobhunt/gateway/client.py`:
   - Use the OpenAI Python SDK pointed at http://localhost:11434/v1 with api_key="ollama"
   - `async def complete(task: str, messages: list[dict], schema: dict | None = None, **opts) -> CompletionResult`
   - When schema is provided, set `response_format={"type": "json_schema", "json_schema": {...}}` (Ollama supports this via the `format` parameter; verify against the installed Ollama version and adapt if needed)
   - Return a CompletionResult with: text, parsed (if schema), tokens_in, tokens_out, latency_ms, model
   - Disk cache responses to `data/cache/llm/<sha256(model+messages+schema)>.json`, opt-out via `cache=False`

2. `src/jobhunt/gateway/router.py`:
   - `resolve(task: str) -> ModelSpec` reading from config `[gateway.tasks]` table mapping task→model
   - Defaults: score→qwen3:8b, tailor→qwen3:14b, cover→qwen3:14b, qa→qwen3:8b, embed→nomic-embed-text
   - ModelSpec includes: model tag, temperature, num_ctx, max_tokens

3. `src/jobhunt/gateway/prompts.py`:
   - Load markdown files from `kb/prompts/` with frontmatter (use `python-frontmatter`)
   - Frontmatter schema: name, description, model_task, temperature, max_tokens, schema (path to JSON schema file)
   - Body supports `{{var}}` substitution from a context dict (use Jinja2; sandbox the environment)
   - `compose(prompt_name: str, context: dict) -> ComposedPrompt` returns messages list + opts ready for client.complete

4. Create starter prompts in kb/prompts/ — these are templates the user will refine:
   - `score.md` — system prompt for fit-scoring; expects {{job_description}}, {{profile_summary}}, {{skills}}; returns JSON via schema
   - `tailor.md` — system prompt for resume tailoring; expects {{job_description}}, {{work_history}}, {{stories}}
   - `cover.md` — cover letter generator; expects {{job_description}}, {{company_brief}}, {{voice_sample}}, {{relevant_stories}}
   - `qa.md` — application form question answerer; expects {{question}}, {{job_description}}, {{relevant_history}}

5. Create starter schemas in kb/schemas/:
   - `score.json` — JSON schema for {score:int 0-100, reasons:[str], red_flags:[str], must_clarify:[str]}
   - `tailored_resume.json` — JSON schema for the structured resume output
   - Each prompt's frontmatter references its schema by relative path

6. `src/jobhunt/kb_loader.py`:
   - `load_profile() -> ProfileBundle` reading all of kb/profile/*.md and exposing them as named strings
   - `relevant_stories(job_description: str, k=3) -> list[str]` using nomic-embed-text via the gateway, with a tiny in-memory cosine search (no vector DB needed at this scale)

7. Wire commands:
   - `jobhunt model list` — shells to `ollama list`, parses, displays as table with VRAM estimate
   - `jobhunt model test <prompt-name>` — composes the prompt with a fixture context, runs it, prints output + stats
   - `jobhunt model pull <tag>` — wrapper for `ollama pull`

8. Eval harness in `src/jobhunt/eval.py` and `evals/`:
   - Eval case format: markdown file in `evals/<prompt-name>/<case-name>.md` with frontmatter (context vars) and an `## Expected` section describing assertions
   - Assertions supported: contains_keyword, score_in_range, json_field_present, no_fabrication (checks output doesn't introduce names/dates not in profile)
   - `jobhunt eval run [--prompt NAME]` — runs cases, prints PASS/FAIL table, exits non-zero if any fail
   - Add 3 starter eval cases for `score` covering: clear strong match, clear weak match, ambiguous middle

9. Tests:
   - Unit tests for prompts.compose with various contexts (Jinja sandboxing, missing vars handled)
   - Unit test for router resolving with config overrides
   - Mocked-client test for complete() respecting cache

End with "Decisions made", and explicitly confirm: gateway is the only place that talks to Ollama; no other module imports openai or httpx-to-ollama directly.
```

### Verification

```bash
ollama pull qwen3:8b
ollama pull qwen3:14b
ollama pull nomic-embed-text
uv run jobhunt model list                       # shows all three
uv run jobhunt model test score                 # runs and prints output + tps
uv run jobhunt eval run                         # passes 3/3 starter cases
```

### Exit criteria

`model test` produces structured JSON for `score`. Eval suite green. No direct Ollama calls outside `gateway/`. Commit.

---

## Phase 3 — Scoring pipeline

> Goal: fit-score every unscored job in the DB.

### Prompt for Claude Code

```
Implement Phase 3 from PLAN.md.

Tasks:

1. `src/jobhunt/pipeline/score.py`:
   - `async def score_job(conn, job_id) -> Score` — loads job + profile, composes `score` prompt, calls gateway, parses JSON, persists to scores table
   - `async def score_unscored(conn, limit=None, concurrency=2) -> ScoreRunStats` — batched with bounded concurrency (don't exceed VRAM by parallelizing too hard)

2. Migration `migrations/0002_scores.sql`:
   - `scores` table: job_id (FK), score INT, reasons JSON, red_flags JSON, must_clarify JSON, model TEXT, prompt_hash TEXT, scored_at TIMESTAMP
   - Index on score for fast filtering

3. Wire commands:
   - `jobhunt score [--all|--unscored] [--limit N]` — runs scorer, shows progress bar (use `rich.progress`)
   - `jobhunt list` gains `--min-score N` and `--max-score N` flags joining the scores table
   - `jobhunt show <id>` displays score, reasons, red flags

4. Refine `kb/prompts/score.md` based on what you learn from the eval suite:
   - Add explicit instruction to surface red_flags (e.g., "requires 10+ years X when profile shows 3")
   - Add "must_clarify" for ambiguous requirements that the user should investigate

5. Add 5 more eval cases for `score` covering edge cases:
   - Senior role for a junior profile (should score low, red_flag the gap)
   - Adjacent skill match (related but not exact tech stack)
   - Remote-required when profile prefers in-office (should red_flag)
   - Bait-and-switch description (talks about "fast paced startup" but is enterprise sales)
   - Perfect-fit baseline

6. Tests: unit tests for score persistence, list filtering with score joins.

End with "Decisions made".
```

### Verification

```bash
uv run jobhunt score --unscored
uv run jobhunt list --min-score 75 --limit 20
uv run jobhunt show <some-id>                   # displays full score detail
uv run jobhunt eval run --prompt score          # 8/8 green
```

### Exit criteria

All current jobs scored. List filtering by score works. Eval suite still green. Commit.

---

## Phase 4 — Tailoring pipeline

> Goal: per-job resume tailoring + cover letter generation.

### Prompt for Claude Code

```
Implement Phase 4 from PLAN.md.

Tasks:

1. `src/jobhunt/pipeline/tailor.py`:
   - `async def tailor_resume(conn, job_id) -> Path` — composes `tailor` prompt with profile + job, gets structured output, renders to `data/applications/<job-id>/resume.md`
   - `async def write_cover_letter(conn, job_id) -> Path` — composes `cover` prompt with relevant_stories(), voice sample, company brief; writes to `data/applications/<job-id>/cover-letter.md`

2. Migration `migrations/0003_applications.sql`:
   - `applications` table: id, job_id (FK), status TEXT (one of: drafted, applied, interviewing, offer, rejected, withdrawn), resume_path, cover_path, fill_plan_path, applied_at, notes TEXT

3. Wire commands:
   - `jobhunt tailor <job-id>` — runs both tailor + cover, creates the application row in `drafted` status, prints paths
   - `jobhunt cover <job-id> --regen` — regenerates only the cover letter
   - `jobhunt show <job-id>` now also shows application status if one exists

4. Refine `kb/prompts/tailor.md`:
   - Hard rule: never invent employers, dates, titles, or metrics
   - Instruction: prefer reordering and rephrasing existing bullets over composing new ones
   - Output: structured JSON (one section per resume part) so we can render deterministically

5. Refine `kb/prompts/cover.md`:
   - Use {{voice_sample}} for tone
   - Reference one specific story from {{relevant_stories}}
   - Hard cap: 250 words. Three paragraphs. No "I am writing to apply for..." opener.

6. Eval cases for tailor and cover:
   - tailor: 3 cases, assert keyword overlap with JD ≥ 60%, no_fabrication passes
   - cover: 3 cases, assert word count in 180–280, contains a story reference, no banned opener phrases

7. Tests for path creation, status transitions, regen flow.

End with "Decisions made".
```

### Verification

```bash
# Make sure kb/profile/*.md is filled in for real before this
uv run jobhunt tailor <some-high-score-job-id>
cat data/applications/<id>/resume.md
cat data/applications/<id>/cover-letter.md
uv run jobhunt eval run                         # all green
```

### Exit criteria

Tailoring produces non-fabricated, JD-aligned resumes and 250-word cover letters in your voice. Eval suite green. Commit.

---

## Phase 5 — Review TUI

> Goal: an interactive daily-driver loop.

### Prompt for Claude Code

```
Implement Phase 5 from PLAN.md.

Build `jobhunt review` using `textual` (preferred) or `prompt_toolkit`. Layout:

- Top: job title, company, location, score, source, posted-at
- Middle-left: job description (scrollable)
- Middle-right: score breakdown (reasons, red_flags, must_clarify)
- Bottom: keybindings: a=apply (runs tailor + opens autofill phase 6 stub), s=skip, t=tailor only, o=open URL in browser, n=note, q=quit

Selection logic: highest score first, fall back to ingestion recency, exclude already-actioned (status != null in applications table or "skipped" tag).

Add a `jobhunt review --queue` command that just prints what the next 10 reviews would be.

Tests for the queue selection logic (deterministic given fixtures).

End with "Decisions made".
```

### Verification

Run `jobhunt review`. Cycle through 5 jobs. Take an action on each. Confirm the DB reflects each action.

### Exit criteria

`review` is the command you actually want to use every morning. Commit.

---

## Phase 6 — Autofill

> Goal: Playwright fills the form. Human submits. Re-read CLAUDE.md "Browser automation rules".

### Prompt for Claude Code

```
Implement Phase 6 from PLAN.md.

1. `src/jobhunt/browser/autofill.py`:
   - Detect ATS by URL pattern (greenhouse.io, lever.co, ashby.com, myworkdayjobs.com)
   - One handler per ATS in `browser/handlers/<ats>.py` implementing `async def fill(page, application_bundle)`
   - Each handler maps known field labels/selectors to profile fields and the tailored doc paths
   - Generate `fill-plan.json` BEFORE filling; print the plan; require user confirmation unless `--yes`
   - NEVER call any submit button. Locate it, log its presence, leave it for the human.

2. Implement Greenhouse, Lever, Ashby handlers. Workday is best-effort (its DOM is hostile); document limits.

3. Wire `jobhunt apply <job-id>`:
   - If no application row exists in `drafted`, run tailor first
   - Open browser headed, navigate to URL, run handler
   - On unknown field, log it to `data/applications/<id>/unfilled-fields.json` for human attention
   - Update application status to `applied` only after the human confirms via `jobhunt status <id> --set applied`. The autofill alone does NOT mark applied.

4. Add `jobhunt apply --dry-run <id>` that produces fill-plan.json without launching a browser.

5. Tests with Playwright fixtures (record HAR or use saved HTML snapshots) for each handler.

End with "Decisions made". Confirm: zero submit-button calls anywhere in the codebase. Grep your own code for `submit` and verify.
```

### Verification

Pick three jobs (one each Greenhouse/Lever/Ashby). Run `jobhunt apply <id>` for each. Confirm: browser opens, fields are filled, NO submit happens, fill-plan.json exists.

### Exit criteria

≥80% of fields auto-filled correctly across 3 ATSes. No submission code anywhere. Commit.

---

## Phase 7 — Tracking + insights

> Goal: close the loop on outcomes.

### Prompt for Claude Code

```
Implement Phase 7 from PLAN.md.

1. `jobhunt status <job-id> --set <state> [--note "..."]` writes to applications.status with timestamp.

2. `jobhunt digest [--days 7]` prints:
   - Counts by status
   - Top sources by application count, by interview rate
   - Score distribution of applied vs interviewed
   - Average time from drafted → applied → response

3. `jobhunt insights` runs the eval suite + a quality check:
   - Are recent cover letters drifting from voice? (embedding similarity to voice.md)
   - Are scores well-calibrated? (compare scores of applied jobs that interviewed vs didn't)

4. Add a `jobhunt export --format jsonl` for backup.

End with "Decisions made".
```

### Verification

After 2 weeks of real use: `jobhunt digest` produces useful numbers; `jobhunt insights` flags real drift if you tinkered with prompts.

### Exit criteria

You can answer "is this thing actually working" with one command. Commit, tag v1.0.

---

## Cross-phase rules for Claude Code

1. Before starting any phase, run `git status` to confirm a clean tree, then create a branch `phase-N-<slug>`.
2. After completing a phase, run the full verification checklist. Don't ask the user to run it for you — run it yourself, paste the output.
3. Open a PR-style summary in the final response: what changed, what you decided, what's risky.
4. If a phase is too large for one session, split it into 4a/4b and surface the seam — don't half-finish silently.
5. When unsure between two designs, pick the simpler one and note the alternative in "Decisions made".
6. Never modify `kb/profile/*.md`. That's the human's domain.
7. Never add a cloud LLM provider to runtime code, no matter how convenient.
