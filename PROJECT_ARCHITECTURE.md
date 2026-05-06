# job-seeker Project Architecture & Detailed Element Breakdown

**Last Updated:** May 6, 2026

This document provides an exhaustive reference of every module, class, function, and responsibility in the `job-seeker` codebase. Organized by functional domain.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Entry Point & CLI](#entry-point--cli)
3. [Core Infrastructure](#core-infrastructure)
4. [Commands (User-Facing)](#commands-user-facing)
5. [Ingestion Layer (Job Sources)](#ingestion-layer-job-sources)
6. [Pipeline (Scoring, Tailoring, Cover Letters)](#pipeline-scoring-tailoring-cover-letters)
7. [Browser Automation](#browser-automation)
8. [Resume Processing](#resume-processing)
9. [Gateway (Ollama Integration)](#gateway-ollama-integration)
10. [Database & Migrations](#database--migrations)
11. [Configuration & Secrets](#configuration--secrets)
12. [Data Models](#data-models)
13. [Error Handling](#error-handling)
14. [Testing & Test Fixtures](#testing--test-fixtures)

---

## Architecture Overview

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────┐
│                   jobhunt CLI Entry Point                    │
│                    (src/jobhunt/cli.py)                      │
└─────────────────────┬───────────────────────────────────────┘
                      │
    ┌─────────────────┴─────────────────┬─────────────────┬──────────────┐
    │                                     │                 │              │
┌───▼────────────┐  ┌──────────────┐  ┌─▼──────────┐  ┌──▼─────────┐  ┌─▼───────────┐
│ convert-resume │  │    scan      │  │  apply     │  │   list     │  │ db / config │
│ (P1)           │  │ (P2)         │  │  (P3+P4)   │  │  (P5)      │  │ (hidden)    │
└────────────────┘  └──────────────┘  └────────────┘  └────────────┘  └─────────────┘
       │                    │                 │              │
       │                    │                 │              │
    ┌──▼──────┐   ┌────┬───┴──┐    ┌────┬───┴────┐    ┌────▼────┐
    │  Resume │   │    │      │    │    │        │    │   DB    │
    │ Parsing │   │    │      │    │    │        │    │  Query  │
    │         │   │    │      │    │    │        │    │         │
    └─────────┘   │    │      │    │    │        │    └─────────┘
                  │    │      │    │    │        │
                  ▼    ▼      ▼    ▼    ▼        ▼
            ┌───────────────────────────────────────┐
            │      Ingestion Layer (8 sources)     │
            │  (greenhouse, lever, ashby, etc.)    │
            └────────┬────────────────────────────┘
                     │
                     ▼
            ┌───────────────────┐
            │    GTA Filter     │
            │  (location-based)  │
            └────────┬──────────┘
                     │
                     ▼
            ┌───────────────────────────────┐
            │  Database Upsert (jobs table) │
            └────────┬──────────────────────┘
                     │
                     ▼
            ┌───────────────────────────────────────┐
            │  Pipeline: Score, Tailor, Cover, Audit│
            │   (via Ollama gateway)                │
            └───────────────────────────────────────┘
                     │
                     ▼
            ┌───────────────────────────────────────┐
            │  Resume/Cover Rendering (.docx/.md)  │
            └────────┬──────────────────────────────┘
                     │
                     ▼
            ┌───────────────────────────────────────┐
            │  Browser Autofill (Playwright)        │
            │  (human submits)                       │
            └───────────────────────────────────────┘
```

### Design Principles

- **Local-first at runtime:** All LLM calls route through `jobhunt.gateway` to Ollama on localhost.
- **Honesty by construction:** Tailoring constrained to `verified.json`. No fabrication.
- **Single hot model:** `qwen3.5:9b` for all tasks (score, tailor, cover, qa).
- **Deterministic post-processing:** Score clamp, cover validator + retry, audit layer.
- **Human-in-the-loop submission:** Playwright fills fields; user clicks Submit.
- **Plain SQL, no ORM:** All DB logic uses parameterized `sqlite3` calls.

---

## Entry Point & CLI

### `src/jobhunt/cli.py`

**Responsibility:** Typer application definition and error handling. Acts as the CLI entry point. Routes user commands to the appropriate command modules.

**Key Elements:**

- **`app: typer.Typer`** — Main Typer application object with help text and command registration.
- **`main(ctx, debug, verbose)` → callback** — Global options:
  - `--debug`: Show full tracebacks on `JobHuntError` (default: False).
  - `--verbose` / `-v`: Raise logging level for more detail (default: False).
  - Stores `{"debug": bool, "verbose": bool}` in `ctx.obj` for downstream use.
- **`_run()` → None** — Wrapper for `app()` that catches `JobHuntError` and exits with code 1, optionally re-raising if `--debug` is set.

**Command Registration:**

- `convert-resume` → `convert_resume_cmd.run`
- `scan` → `scan_cmd.run`
- `apply` → `apply_cmd.run`
- `list` → `list_cmd.run`
- `db` → `db_cmd.app` (typer subgroup, hidden)
- `config` → `config_cmd.app` (typer subgroup, hidden)

---

## Core Infrastructure

### `src/jobhunt/http.py`

**Responsibility:** Shared async HTTP client with per-host rate limiting, exponential backoff, and redirect following. Used by all ingest adapters.

**Key Elements:**

- **`RateLimiter` class:**
  - `__init__(rate_per_sec: float)` — Initialize with requests per second (e.g., 1.0).
  - `async wait(host: str)` — Block until the next request to `host` is allowed. Uses per-host locks to serialize requests.
  - Internal state: `_min_interval` (computed from rate), `_last` (last request time per host), `_locks` (per-host async locks).

- **`host_of(url: str) → str`** — Extract hostname from URL using `urllib.parse`.

- **`async get_json(client, url, limiter, *, params=None, max_retries=3) → dict`**
  - GET a URL with JSON response, respecting rate limit.
  - Retries with exponential backoff on 429 or 5xx.
  - Raises `IngestError` on 404 or after `max_retries` attempts.
  - Strips trailing `?` from JSON responses.

- **`async post_json(client, url, limiter, *, json_body, max_retries=3) → dict`**
  - POST with JSON body, respecting rate limit.
  - Retries with exponential backoff on 429 or 5xx.
  - Raises `IngestError` on 401/403 (auth-walled), 404, or after retries.

- **`async resolve_redirect(client, url, limiter, *, max_hops=5) → str`**
  - Follow HTTP redirect chain (Location header) up to `max_hops` times.
  - Used to resolve Adzuna's tracking links to the actual employer posting.
  - Never raises; on any error (network, loop, non-redirect status, timeout), returns original URL.
  - Tries HEAD first; falls back to streaming GET if 405 (HEAD not allowed).

- **`async with_client[T](fn, *, user_agent=DEFAULT_UA) → T`**
  - Context manager that creates an `httpx.AsyncClient` with the given user-agent and passes it to `fn`.
  - Used by ingest adapters to get a client: `await with_client(lambda c: fetch(c, ...))`.

- **`DEFAULT_UA = "job-seeker/0.1 (+personal-use; caseyhsu@proton.me)"`** — User-Agent string.

### `src/jobhunt/db.py`

**Responsibility:** SQLite connection management, migration runner, and query helpers. All database interactions go through this module.

**Key Elements:**

- **`connect(db_path: Path) → sqlite3.Connection`**
  - Create/connect to SQLite database at `db_path`.
  - Sets `row_factory = sqlite3.Row` (dict-like rows).
  - Enables `PRAGMA journal_mode=WAL` (Write-Ahead Logging) for concurrent access.
  - Enables `PRAGMA foreign_keys=ON` for referential integrity.

- **`migrate(conn, migrations_dir) → MigrationResult`**
  - Apply all unapplied migrations in `migrations_dir` in order.
  - Tracks applied migrations in a `migrations` table.
  - Raises `MigrationError` if directory missing or migration fails.
  - Returns `MigrationResult(applied: list[str], skipped: list[str])`.

- **`_ensure_migrations_table(conn) → None`** — Create the `migrations` tracking table if it doesn't exist.

- **`MIGRATION_FILE_RE = r"^(\d{4})_[a-zA-Z0-9_]+\.sql$"`** — Regex to match migration filenames (e.g., `0001_init.sql`).

**Query Helpers (all parameterized to prevent SQL injection):**

- **`upsert_job(conn, job: Job) → bool`**
  - Insert a job into the `jobs` table. Returns `True` if inserted (new), `False` if ignored (duplicate by source + external_id).
  - Uses `INSERT OR IGNORE` to avoid duplicates.

- **`unscored_jobs(conn, limit=None) → list[sqlite3.Row]`**
  - Fetch all jobs that have never been scored (no entry in `scores` table).
  - Ordered by `ingested_at DESC` (most recent first).

- **`jobs_to_score(conn, *, current_hash: str, limit=None) → list[sqlite3.Row]`**
  - Fetch jobs that need scoring: never scored, or scored with a different prompt hash.
  - Each row includes a `prev_hash` column: NULL for new, a string for stale.
  - Useful to count new vs. rescore jobs.

- **`upsert_application(conn, *, application_id, job_id, status, resume_path, cover_path, fill_plan_path, applied_week, notes=None) → None`**
  - Insert or update an application. Sets `applied_at = CURRENT_TIMESTAMP` only when status transitions to 'applied' and was previously NULL.
  - Sets `outcome_at = CURRENT_TIMESTAMP` when status transitions to a terminal status (interviewing, offer, rejected, withdrawn).

- **`set_decline_reason(conn, job_id, reason: str | None) → None`** — Set `decline_reason` on a job (auto-decline logic).

- **`write_score(conn, *, job_id, score, reasons, red_flags, must_clarify, model, prompt_hash) → None`**
  - Insert or replace a score record. Stores reasons/red_flags/must_clarify as JSON strings.

### `src/jobhunt/secrets.py`

**Responsibility:** Load API keys from `~/.config/jobhunt/secrets.toml` or environment variables. Never logs secrets.

**Key Elements:**

- **`Secrets` (Pydantic BaseModel):**
  - `adzuna_app_id: str | None` — Adzuna application ID.
  - `adzuna_app_key: str | None` — Adzuna application key.

- **`secrets_path() → Path`** — Return `~/.config/jobhunt/secrets.toml`.

- **`load_secrets() → Secrets`**
  - Load secrets from TOML file (if exists) and environment (prefix `JOBHUNT_`).
  - Environment variables override TOML.
  - Returns a validated `Secrets` object.

### `src/jobhunt/config.py`

**Responsibility:** Config loading and validation. Single source of truth: `~/.config/jobhunt/config.toml`. Environment variables (prefix `JOBHUNT_`) override TOML.

**Key Elements:**

- **`PathsConfig` (Pydantic BaseModel):**
  - `data_dir: Path` — Directory for local data (default: `./data`).
  - `db_path: Path` — SQLite database path (default: `data/jobhunt.db`).
  - `migrations_dir: Path` — Path to SQL migrations (default: `./migrations`).
  - `kb_dir: Path` — Knowledge base directory (default: `./kb`).

- **`AdzunaConfig`:**
  - `queries: list[str]` — Search terms (e.g., "javascript developer").
  - `pages: int` — Number of pages to fetch (default: 3).
  - `results_per_page: int` — Results per page (default: 50).

- **`IngestConfig`:**
  - `user_agent: str` — User-Agent string (default identifies as job-seeker).
  - `rate_limit_per_sec: float` — HTTP rate limit per host (default: 1.0).
  - `cache_ttl_hours: int` — Cache TTL for raw API responses (default: 6).
  - `greenhouse: list[str]` — Greenhouse boards to ingest (e.g., ["stripe", "shopify"]).
  - `lever: list[str]` — Lever slugs (e.g., ["benchsci", "ada"]).
  - `ashby: list[str]` — Ashby slugs.
  - `smartrecruiters: list[str]` — SmartRecruiters slugs.
  - `workday: list[str]` — Workday tenant specs in format "tenant:host:site".
  - `job_bank_ca: list[str]` — Job Bank Canada RSS feed URLs.
  - `rss: list[str]` — Generic employer RSS feed URLs.
  - `adzuna: AdzunaConfig` — Adzuna-specific settings.

- **`GatewayConfig`:**
  - `base_url: str` — Ollama endpoint (default: `http://localhost:11434/v1`).
  - `api_key: str` — Ollama API key (default: "ollama").
  - `tasks: dict[str, str]` — Model assignments (score, tailor, cover, qa, embed).

- **`PipelineConfig`:**
  - `score_concurrency: int` — Concurrent scoring tasks (default: 2).
  - `tailor_max_words: int` — Max words in tailored resume (default: 700).
  - `cover_max_words: int` — Max words in cover letter (default: 280).
  - `cover_retry_attempts: int` — Retry cover letter generation on violations (default: 3).
  - `min_score: int` — Minimum score to include in `apply --best` (default: 65).

- **`BrowserConfig`:**
  - `headed: bool` — Run Playwright headed (default: True).
  - `user_data_dir: Path` — Browser profile directory (default: `data/browser-profile`).

- **`ApplicantProfile`:**
  - `full_name, email, phone, linkedin_url, github_url, portfolio_url, city, region, country`
  - `work_auth_canada: bool` — Work authorization status.
  - `requires_visa_sponsorship: bool`
  - `salary_expectation_cad: str` — e.g., "100k–120k".
  - `pronouns: str` — Optional pronouns.

- **`Config` (top-level model):**
  - Aggregates all above: `paths, ingest, gateway, pipeline, browser, applicant`.
  - `Config.example_toml() → str` — Generate example TOML for init.

**Functions:**

- **`load_config(path=None, *, write_default_if_missing=True) → Config`**
  - Load config from file (default: `~/.config/jobhunt/config.toml`).
  - If file missing and `write_default_if_missing=True`, create it with example.
  - Apply environment variable overrides (prefix `JOBHUNT_`, separator `__`).
  - Example: `JOBHUNT_GATEWAY__BASE_URL=http://ollama:11434/v1` overrides `config.gateway.base_url`.

- **`config_path() → Path`** — Return config file path.

- **`_apply_env_overrides(data) → dict`** — Recursively apply env var overrides to config dict.

- **`_to_toml_dict(obj) → dict`** — Recursively coerce values to TOML-safe types (Path → str, etc.).

---

## Commands (User-Facing)

### `src/jobhunt/commands/convert_resume_cmd.py` — P1: Parse Resume

**Responsibility:** Parse `Casey_Hsu_Resume_Baseline.docx` into `kb/profile/verified.json` and markdown files. This is the source of truth for all tailoring.

**Flow:**

```
Casey_Hsu_Resume_Baseline.docx
  ↓
parse_baseline() → VerifiedFacts
  ↓
├→ write_verified_json() → kb/profile/verified.json
├→ write_kb_markdown() → kb/profile/{resume,skills,work-history,education}.md
```

**Key Code:**

```python
@app.callback(invoke_without_command=True)
def run(
    docx: Path = typer.Option(Path("Casey_Hsu_Resume_Baseline.docx"), "--docx", ...)
) -> None:
    cfg = load_config()
    facts = parse_baseline(docx)
    
    verified = cfg.paths.kb_dir / "profile" / "verified.json"
    write_verified_json(facts, verified)
    written = write_kb_markdown(facts, cfg.paths.kb_dir)
    
    # Print summary
    typer.echo(f"verified facts: {verified}")
    for p in written:
        typer.echo(f"regenerated:    {p}")
```

**Output:**

- `kb/profile/verified.json` — Structured facts (skills, work history, certifications, etc.).
- `kb/profile/resume.md` — Formatted resume markdown.
- `kb/profile/skills.md` — Skill taxonomy.
- `kb/profile/work-history.md` — Formatted work history.
- `kb/profile/education.md` — Education & certifications.

---

### `src/jobhunt/commands/scan_cmd.py` — P2: Ingest & Score

**Responsibility:** Fetch jobs from all configured sources, deduplicate, filter by GTA, store in DB, then score unscored jobs using Ollama.

**Flow:**

```
Config: ingest.{greenhouse, lever, ashby, ...}
  ↓
[Parallel] Fetch from each source
  ↓
GTA Filter (location check)
  ↓
DB upsert (INSERT OR IGNORE by source+external_id)
  ↓
Load unscored jobs
  ↓
[Concurrent, limit=config.pipeline.score_concurrency] score_job() via Ollama
  ↓
DB write_score() + set_decline_reason() if auto-decline
```

**Key Code:**

```python
@app.callback(invoke_without_command=True)
def run(
    skip_score: bool = typer.Option(False, "--skip-score", ...),
    skip_ingest: bool = typer.Option(False, "--skip-ingest", ...),
    limit: int | None = typer.Option(None, "--limit", ...),
) -> None:
    cfg = load_config()
    asyncio.run(_run(cfg, skip_score=skip_score, skip_ingest=skip_ingest, limit=limit))
```

**Options:**

- `--skip-score`: Ingest only; don't score (useful for volume testing).
- `--skip-ingest`: Score the backlog only (re-score under a new prompt).
- `--limit N`: Cap scoring to N jobs (useful for testing).

**Output:**

- Jobs inserted into `jobs` table.
- Scores inserted into `scores` table with prompt_hash, model, reasons, red_flags, must_clarify.
- `decline_reason` set on jobs with auto-decline triggers.

---

### `src/jobhunt/commands/apply_cmd.py` — P3+P4: Tailor, Cover, Autofill

**Responsibility:** Generate tailored resume and cover letter for selected jobs, render to .docx/.md, run browser autofill, log fill-plan.json, mark status=drafted.

**Flow (per job):**

```
Job ID (selected by user: single, --top N, or --best)
  ↓
tailor_resume() → TailoredResume (via Ollama)
  ↓
write_cover() → CoverLetter (via Ollama)
  ↓
audit() → AuditResult (deterministic, no LLM)
  │ └─→ Verdict: ship | revise | block
  ├→ If block: skip job, log reason
  ├→ If revise: warn to stderr, write audit.json, continue
  └→ If ship: continue
  ↓
render_docx() → data/applications/<job-id>/Casey_Hsu_Resume_<RoleSlug>.docx
render_cover_docx() → data/applications/<job-id>/cover-letter.md
  ↓
[if --browser (default)] autofill.run() → Opens Playwright headed browser
  │ └─→ Detects ATS (Greenhouse, generic fallback)
  │ └─→ Fills fields from ApplicantProfile
  │ └─→ Logs fill-plan.json
  │ └─→ Waits for human to click Submit
  ↓
upsert_application(status=drafted) → DB
  ├→ application_id = gen UUID
  ├→ resume_path, cover_path, fill_plan_path = paths
  └→ applied_week = ISO week label
```

**Key Code:**

```python
@app.callback(invoke_without_command=True)
def run(
    job_id: str | None = typer.Argument(None, ...),
    top: int | None = typer.Option(None, "--top", min=1, max=10, ...),
    best: bool = typer.Option(False, "--best", ...),
    set_status: str | None = typer.Option(None, "--set-status", ...),
    min_score: int | None = typer.Option(None, "--min-score", ...),
    no_browser: bool = typer.Option(False, "--no-browser", ...),
    headless: bool = typer.Option(False, "--headless", ...),
) -> None:
    # Selection modes (mutually exclusive)
    if job_id:
        # Single job
    elif top is not None:
        # Auto-pick top N
    elif best:
        # Interactive picker
    else:
        # Error: need one of the above
    
    # For each selected job:
    #   1. tailor_resume()
    #   2. write_cover_with_retry()
    #   3. audit()
    #   4. render .docx/.md
    #   5. autofill() if --browser (default)
    #   6. upsert_application()
```

**Options:**

- `<job-id>`: Single job ID.
- `--top N`: Auto-pick N best unapplied (capped at 10).
- `--best`: Interactive picker from top 10.
- `--set-status {drafted|applied|interviewing|offer|rejected}`: Update status without re-tailoring.
- `--min-score N`: Override `config.pipeline.min_score` for this run.
- `--no-browser`: Generate docs without opening browser.
- `--headless`: Run browser headless (dry-run mode).

**Output:**

- `data/applications/<job-id>/`
  - `tailored-resume.json` — Raw tailored resume output from Ollama.
  - `Casey_Hsu_Resume_<RoleSlug>.docx` — Rendered resume (ATS-safe).
  - `cover-letter.md` — Markdown cover letter.
  - `cover-letter.docx` — Rendered cover letter (if enabled).
  - `fill-plan.json` — Log of browser autofill actions.
  - `audit.json` — Post-generation audit report (if verdict != ship).
- DB: `applications` table marked with status=drafted, paths, applied_week.

---

### `src/jobhunt/commands/list_cmd.py` — P5: Pipeline View

**Responsibility:** Display scored jobs and weekly application pipeline. Supports filtering by week, status, min_score, source.

**Flow:**

```
DB query: SELECT j.*, s.score, a.status, a.applied_at ...
  ├─→ WHERE applied_week = <target_week> (if --week)
  ├─→ WHERE a.status = <status> (if --status)
  ├─→ WHERE s.score >= <min_score> (if --min-score)
  ├─→ WHERE j.source = <source> (if --source)
  └─→ LIMIT <limit> (default: 40)
  ↓
Render rows in table format
  ↓
Print weekly rollup footer (counts by status, decline reason, etc.)
```

**Key Code:**

```python
@app.callback(invoke_without_command=True)
def run(
    week: int | None = typer.Option(None, "--week", ...),
    status: str | None = typer.Option(None, "--status", ...),
    min_score: int | None = typer.Option(None, "--min-score", ...),
    source: str | None = typer.Option(None, "--source", ...),
    limit: int = typer.Option(40, "--limit", ...),
) -> None:
    cfg = load_config()
    conn = connect(cfg.paths.db_path)
    try:
        target_week = _iso_week_label(week) if week is not None else None
        rows = _query(conn, week_label=target_week, status=status, ...)
        _render_rows(rows, target_week)
        typer.echo("")
        _render_weekly_footer(conn, target_week or _iso_week_label(0))
    finally:
        conn.close()
```

**Options:**

- `--week N`: Filter to week N (0=current, 1=last, ...).
- `--status {drafted|applied|interviewing|offer|rejected}`: Filter by status.
- `--min-score N`: Minimum score.
- `--source {greenhouse|lever|ashby|adzuna_ca|...}`: Job source.
- `--limit N`: Max rows (default: 40).

**Output:**

- Table with columns: ID | Source | Title | Company | Location | Score | Status | Applied_At.
- Weekly footer with counts: scanned, declined, drafted, applied, interviewing, offer, rejected.

---

## Ingestion Layer (Job Sources)

**Overview:** Eight adapter modules, each with an `async def fetch(client, limiter, ...) -> AsyncIterator[Job]` signature. All filter by GTA location before yielding.

### Location Filtering: `src/jobhunt/ingest/_filter.py`

**Responsibility:** Classify remote type and validate GTA eligibility.

**Key Elements:**

- **`RemoteType`** — Literal["onsite", "hybrid", "remote", "unknown"].

- **`GTA_CITIES` tuple** — 19 cities + surrounding areas (Toronto, Mississauga, Brampton, etc.) + Kitchener-Waterloo corridor.

- **`_NON_CANADA_REMOTE` regex** — Matches "USA", "Europe", "EMEA", etc. (excludes as remote).

- **`_CANADA_HINT` regex** — Matches "Canada", "Ontario", "Toronto", "EST", or comma-delimited "ON" (includes as Canadian).

- **`classify_remote_type(location, extra=None) → RemoteType`**
  - Parses location string and optional extra text (e.g., commitment type from Lever).
  - Returns "remote" if location contains "remote" + Canada hint.
  - Returns "hybrid" if location contains "hybrid".
  - Returns "onsite" if location matches GTA city.
  - Returns "unknown" otherwise.

- **`is_gta_eligible(location: str) → bool`**
  - Returns `True` if location is GTA city, "Remote-Canada", "Remote-Ontario", "Remote-EST", or matches other Canada + remote patterns.
  - Returns `False` if location contains non-Canada remote keywords (USA, Europe, etc.) or is bare "Remote" (ambiguous).

### RSS/Atom Parser: `src/jobhunt/ingest/_rss.py`

**Responsibility:** Parse RSS 2.0 and Atom 1.0 feeds using stdlib `xml.etree`. No external feed parser dependencies.

**Key Elements:**

- **`ATOM_NS = "{http://www.w3.org/2005/Atom}"`** — Atom namespace prefix.

- **`RSSItem` dataclass:**
  - `title, link, description, pub_date, guid`.

- **`strip_html(text) → str | None`** — Remove HTML tags; collapse whitespace.

- **`parse_feed(xml_text) → Iterator[RSSItem]`**
  - Parse RSS 2.0: iterate `<item>` elements under `<channel>`.
  - Parse Atom 1.0: iterate `<entry>` elements under `<feed>`.
  - Yields one `RSSItem` per entry/item.

- **`async fetch_feed(client, url, limiter, *, max_retries=3) → str`**
  - GET RSS/Atom feed URL with rate-limiting and backoff.
  - Returns raw XML text.

### Greenhouse: `src/jobhunt/ingest/greenhouse.py`

**API:** `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`

**Parameters:** `slug` (e.g., "stripe", "shopify").

**Adapter:**

```python
async def fetch(client: httpx.AsyncClient, limiter: RateLimiter, slug: str) -> AsyncIterator[Job]:
    # GET /v1/boards/{slug}/jobs?content=true
    # For each job:
    #   - Check location via is_gta_eligible()
    #   - Strip HTML from description
    #   - Parse ISO datetime
    #   - Yield Job object
```

**Job Fields Mapped:**

- `id` → `greenhouse:{slug}:{external_id}`
- `company` → slug
- `title` → j.title
- `location` → j.location.name
- `description` → _strip_html(j.content)
- `url` → j.absolute_url
- `posted_at` → ISO datetime from updated_at

### Lever: `src/jobhunt/ingest/lever.py`

**API:** `https://api.lever.co/v0/postings/{slug}?mode=json`

**Parameters:** `slug` (e.g., "benchsci", "ada").

**Adapter:**

```python
async def fetch(client, limiter, slug) -> AsyncIterator[Job]:
    # GET /v0/postings/{slug}?mode=json
    # For each posting:
    #   - Merge location + commitment (if "remote", append to location)
    #   - Check is_gta_eligible()
    #   - Use descriptionPlain or description
    #   - Yield Job object
```

**Job Fields Mapped:**

- `id` → `lever:{slug}:{external_id}`
- `company` → slug
- `title` → j.text
- `location` → categories.location [+ "Remote" if remote commitment]
- `description` → j.descriptionPlain or j.description
- `url` → j.hostedUrl or j.applyUrl
- `posted_at` → milliseconds to datetime

### Ashby: `src/jobhunt/ingest/ashby.py`

**API:** `https://api.ashbyhq.com/posting-api/job-board/{slug}`

**Parameters:** `slug`.

**Adapter:**

```python
async def fetch(client, limiter, slug) -> AsyncIterator[Job]:
    # GET job-board/{slug}
    # For each job:
    #   - Check isRemote; if true + has location, append "(Remote)"
    #   - Check is_gta_eligible()
    #   - Use descriptionPlain or descriptionHtml
    #   - Yield Job object
```

**Job Fields Mapped:**

- `id` → `ashby:{slug}:{external_id}`
- `company` → slug
- `remote_type` → "remote" if isRemote, else classify_remote_type(location)

### Adzuna CA: `src/jobhunt/ingest/adzuna_ca.py`

**API:** `https://api.adzuna.com/v1/api/jobs/ca/search/{page}`

**Parameters:** `app_id`, `app_key` (from secrets), `query`, `pages`, `results_per_page`.

**Flow:**

```
For page 1 to N:
  GET /search/{page}?
    app_id=<app_id>&app_key=<app_key>&
    what=<query>&where=Toronto&distance=100&
    results_per_page=<results_per_page>
  For each result:
    Check location via display_name / area
    Check is_gta_eligible()
    resolve_redirect(redirect_url) → get actual employer URL
    Yield Job object
```

**Key:** Resolves Adzuna's tracking redirect links to the actual employer posting URL at ingest time.

### SmartRecruiters: `src/jobhunt/ingest/smartrecruiters.py`

**API:** `https://api.smartrecruiters.com/v1/companies/{slug}/postings`

**Parameters:** `slug`.

**Adapter:**

```python
async def fetch(client, limiter, slug) -> AsyncIterator[Job]:
    # GET with pagination: offset, limit=100
    # For each posting:
    #   - Format location from location.{city, country, remote}
    #   - Check is_gta_eligible()
    #   - Extract description from jobAd.sections
    #   - Yield Job object
```

**Note:** No auth required for public boards.

### Workday CXS: `src/jobhunt/ingest/workday.py`

**API:** `https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`

**Parameters:** Tenant specs in format "tenant:host:site" (e.g., "rbc:wd3:RBC_Careers").

**Adapter:**

```python
async def fetch(client, limiter, spec, *, max_pages=5) -> AsyncIterator[Job]:
    # Parse spec: tenant, host, site
    # For page 1 to max_pages:
    #   POST /jobs with {"pagesize": 20, "from": offset, "sort": "-posted"}
    #   For each job item:
    #     - Extract location from locationsText or bulletFields
    #     - Check is_gta_eligible()
    #     - Yield Job object
```

**Note:** Targets RBC, TD, BMO, CIBC, Scotia, Manulife, Sun Life, Telus, Bell, Rogers, Loblaw Digital, Thomson Reuters (all Workday-hosted).

### Job Bank Canada: `src/jobhunt/ingest/job_bank_ca.py`

**Source:** Government of Canada Job Bank RSS feeds.

**API:** User-configured RSS URLs in `config.toml` under `[ingest] job_bank_ca`.

**Adapter:**

```python
async def fetch(client, limiter, feed_url) -> AsyncIterator[Job]:
    # GET feed_url (RSS/Atom)
    # For each item:
    #   - Parse title: "Title - Employer - City, Province"
    #   - Check is_gta_eligible(location)
    #   - Extract description (with HTML stripped)
    #   - Yield Job object
```

### Generic RSS: `src/jobhunt/ingest/rss_generic.py`

**Source:** Employer career feeds (e.g., company.com/careers/feed.xml).

**Configuration:** User-provided RSS URLs in `config.toml` under `[ingest] rss`.

**Adapter:**

```python
async def fetch(client, limiter, feed_url) -> AsyncIterator[Job]:
    # GET feed_url
    # For each item:
    #   - Use title as role
    #   - Check is_gta_eligible(description or title blob)
    #   - Yield Job object
```

**Note:** Generic feeds rarely have structured location, so location is inferred from description text.

---

## Pipeline (Scoring, Tailoring, Cover Letters)

### `src/jobhunt/pipeline/score.py` — Fit Scoring

**Responsibility:** Score a job posting against `verified.json` and decide auto-decline.

**Flow:**

```
Job description (truncated to 6000 chars)
  + verified.json (Casey's profile)
  + policy.md (tailoring rules)
  ↓
Load kb/prompts/score.md (with schema)
Render user template with job details
  ↓
POST to Ollama /api/chat (format=schema, temperature=0.0)
  ↓
Parse JSON response → ScoreResult
  ├─ score: int (0–100)
  ├─ matched_must_haves: list[str]
  ├─ gaps: list[str]
  ├─ decline_reason: str | None
  └─ ai_bonus_present: bool
  ↓
Post-processing: _clamp_score()
  - Re-partition claimed must-haves against verified.json
  - If coverage < 60%: clamp score to 64
  - If coverage 60–79%: clamp to 79
  - If coverage 80–99%: clamp to 89
  - If coverage 100%: keep score as-is
```

**Key Elements:**

- **`MAX_DESC_CHARS = 6000`** — Truncate job description to fit `num_ctx`.
- **`MAX_POLICY_CHARS = 4000`** — Truncate policy doc.

- **`truncate(s, limit) → str`** — Truncate string; append "[truncated]" if cut.

- **`ScoreResult` dataclass:**
  - `score: int` — Final score (0–100).
  - `matched_must_haves: list[str]` — Must-haves present in JD + verified profile.
  - `gaps: list[str]` — Must-haves missing.
  - `decline_reason: str | None` — Why this job was auto-declined (if any).
  - `ai_bonus_present: bool` — Whether JD mentions AI/LLM skills (Casey's bonus).
  - `model: str` — Model used for scoring.

- **`async score_job(cfg: Config, job: Job) → ScoreResult`**
  - Load `score.md` prompt.
  - Render user template with job details, verified facts, policy.
  - Call `complete_json()` to Ollama.
  - Apply deterministic clamping.
  - Return result.

- **`prompt_hash(policy_text) → str`** — Hash of the policy document. Used to detect when rescoring is needed (prompt change).

**Auto-Decline Triggers:**

From `kb/prompts/score.md`:

- 3+ must-haves are gaps.
- Experience gap > 2x Casey's years.
- Role title is senior/lead/architect without Casey having senior title.
- Domain is regulated (healthcare, finance compliance).
- Location ineligible (non-GTA, non-Canada-remote).

---

### `src/jobhunt/pipeline/tailor.py` — Resume Tailoring

**Responsibility:** Generate a tailored resume by re-prioritizing, re-framing, and surfacing facts from `verified.json` for a specific JD.

**Flow:**

```
Job description (truncated to 6000 chars)
  + verified.json
  + policy.md
  ↓
Load kb/prompts/tailor.md (with schema)
Render user template
  ↓
POST to Ollama /api/chat (format=schema, temperature=0.3)
  ↓
Parse JSON response → TailoredResume
  ├─ summary: str
  ├─ skills_categories: list[TailoredCategory]
  │   ├─ name: str
  │   └─ items: list[str]
  ├─ roles: list[TailoredRole]
  │   ├─ title, employer, dates, bullets
  ├─ certifications, education, coursework
  └─ model: str
  ↓
Post-processing: _enforce_no_fabrication()
  - Reject any role where (employer, dates) not in verified.json
  - Reject skills not in verified.json (substring tolerance for parens)
  - Reject "Familiar" skills in non-Familiar category
  - If violations: raise PipelineError (blocks apply)
```

**Key Elements:**

- **`TailoredCategory` dataclass:**
  - `name: str` — e.g., "Languages", "Web & Mobile", "DevOps & Cloud".
  - `items: list[str]` — Skill list.

- **`TailoredRole` dataclass:**
  - `title, employer, dates: str`
  - `bullets: list[str]` — Achievement bullets.

- **`TailoredResume` dataclass:**
  - Summary, skills, roles, certs, education, coursework.

- **`async tailor_resume(cfg: Config, job: Job) → TailoredResume`**
  - Load tailor prompt, render user template, call Ollama, parse response.
  - Apply `_enforce_no_fabrication()` invariants.
  - Raise `PipelineError` if violated (blocks apply).

- **`_enforce_no_fabrication(tailored, verified) → None`**
  - Defensive post-decode check.
  - Validates every role is in verified.json.
  - Validates every skill matches (or is substring of) verified skills.
  - Validates "Familiar" skills don't promote to higher categories.
  - Raises `PipelineError` if violated.

---

### `src/jobhunt/pipeline/cover.py` — Cover Letter Generation

**Responsibility:** Generate a tailored cover letter (3–4 paragraphs, 280 words max).

**Flow:**

```
Job description (truncated)
  + verified.json
  ↓
Load kb/prompts/cover.md (schema, temperature=0.7)
Render user template
  ↓
POST to Ollama /api/chat
  ↓
Parse JSON response → CoverLetter
  ├─ salutation: str
  ├─ body: list[str] — 3–4 paragraphs
  ├─ sign_off: str
  └─ model: str
  ↓
Optional: CoverLetter.to_markdown() → string
```

**Key Elements:**

- **`CoverLetter` dataclass:**
  - `salutation, body: list, sign_off, model`.
  - `to_markdown() → str` — Join parts with proper spacing.

- **`async write_cover(cfg, job, *, revisions="") → CoverLetter`**
  - Load cover prompt.
  - Render user template (optionally include `revisions` if retrying).
  - Call Ollama.
  - Return `CoverLetter`.

- **`async write_cover_with_retry(cfg, job, max_attempts=3) → CoverLetter`**
  - Call `write_cover()`, then `validate_cover()`.
  - If violations and attempts < max_attempts: call `write_cover(revisions="...")` with violation hints.
  - On max attempts: return last attempt (audit verdict = revise).

**Temperature:** 0.7 (higher than scoring/tailoring) to allow creative latitude in cover letter tone.

---

### `src/jobhunt/pipeline/cover_validate.py` — Cover Letter Validation

**Responsibility:** Deterministic post-generation audit of cover letters. Catches banned phrases, structural violations, unverified numbers, etc.

**Key Elements:**

- **`BANNED_PHRASES` tuple** — ~25 generic phrases (e.g., "passionate", "synergy", "leveraged", "hit the ground running").

- **`BANNED_OPENERS` tuple** — Form-letter openers (e.g., "applying for", "to whom it may concern").

- **`validate_cover(cover, *, verified, company, max_words) → list[str]`**
  - Returns list of violation strings. Empty = clean.
  - Checks:
    1. Opener not banned.
    2. Company name appears in lead paragraph.
    3. Body doesn't contain banned phrases (case-insensitive).
    4. Word count ≤ max_words.
    5. Paragraph count 3–4.
    6. No unverified numbers (numbers must appear in verified.json).
    7. Sign-off doesn't re-recap education/credentials.

- **`_body_text(cover) → str`** — Join body paragraphs.
- **`_word_count(text) → int`** — Count words.
- **`_verified_numbers(verified) → set[str]`** — Extract all digit clusters from verified.json.

---

### `src/jobhunt/pipeline/audit.py` — Post-Generation Audit

**Responsibility:** Deterministic (LLM-free) audit after tailor + cover generation, before .docx render. Decides verdict: ship | revise | block.

**Flow:**

```
TailoredResume
  + CoverLetter
  + ScoreResult (for must-haves to check)
  + verified.json
  ↓
Check 1: Keyword coverage
  - Render resume markdown
  - For each must-have from score result: phrase_present(must_have, resume_text)?
  - Calculate coverage_pct = matched / total
  - If coverage_pct < 70%: verdict = revise, reason = "keyword coverage"
  
Check 2: Tailor invariants (defensive re-check)
  - _enforce_no_fabrication(tailored, verified)
  - If violated: verdict = block, reason = "fabrication detected"
  
Check 3: Cover-letter validator
  - validate_cover(cover, verified, company, max_words)
  - If violations: verdict = revise, reason = "cover violations"
  
→ AuditResult:
  ├─ keyword_coverage_pct: int
  ├─ matched_keywords: list[str]
  ├─ missing_must_haves: list[str]
  ├─ fabrication_flags: list[str]
  ├─ cover_letter_violations: list[str]
  └─ verdict: str (ship | revise | block)
```

**Key Elements:**

- **`MIN_KEYWORD_COVERAGE_PCT = 70`** — ATS guideline (2026).

- **`AuditResult` dataclass:**
  - `keyword_coverage_pct, matched_keywords, missing_must_haves, fabrication_flags, cover_letter_violations, verdict`.
  - `to_json() → str` — Serialize to JSON.

- **`async audit(cfg, tailored, cover, score_result, job) → AuditResult`**
  - Perform all three checks above.
  - Return aggregate result.

- **`write_audit(result, audit_path) → None`** — Write audit.json.

---

### `src/jobhunt/pipeline/_keywords.py` — Shared Phrase Matching

**Responsibility:** Shared logic for matching phrases in text, used by both score clamp and audit.

**Key Elements:**

- **`phrase_tokens(phrase) → list[str]`**
  - Tokenize phrase, filter stopwords, keep only tokens > 1 char.
  - Stopwords: articles, prepositions, common verbs, etc.

- **`phrase_present(phrase, blob) → bool`**
  - Returns `True` if:
    - Full phrase appears as substring in blob, OR
    - Every non-stopword token in phrase appears somewhere in blob.
  - Used to match JD must-haves against resume text.

---

## Browser Automation

### `src/jobhunt/browser/autofill.py` — Playwright Autofill

**Responsibility:** Open application URL in headed Playwright browser, detect ATS, fill fields with applicant profile, log fill-plan.json, hand off to human.

**Flow:**

```
Browser.goto(job.url)
  ↓
await looks_like_application_page(page)?
  ├─ Yes → run handler
  └─ No → prompt user
  ↓
Handler (Greenhouse-specific or generic fallback):
  - Detect relevant form fields
  - Map ApplicantProfile → field values
  - Fill text inputs
  - Upload resume file
  - Log each action
  ↓
Build fill-plan.json with all actions
  ├─ selector: str
  ├─ profile_key: str
  ├─ value: str
  ├─ kind: str (fill | upload | skipped)
  └─ note: str | None
  ↓
Print "Ready to submit. Press Enter when done." (waits for human)
```

**Hard Rules:**

- **Never click Submit button.** Function blocks before.
- **Never auto-create accounts.** Exit if signup required.
- **Log every fill action** for auditability.
- **Run headed by default** (use `--headless` only for dry-runs).

**Key Elements:**

- **`async looks_like_application_page(page) → bool`**
  - Returns `True` if page likely hosts an application form.
  - Heuristics: autocomplete=given-name, resume file input, textarea hints at cover letter.

- **`async autofill_job(cfg, job, score_result, tailored, cover) → list[FieldFill]`**
  - Build field map from ApplicantProfile.
  - Detect ATS handler (Greenhouse, generic fallback).
  - Run handler to fill fields.
  - Log actions to fill-plan.json.
  - Wait for human confirmation.

---

### `src/jobhunt/browser/profile_map.py` — Field Mapping

**Responsibility:** Map `ApplicantProfile` to a flat key → value dictionary for form handlers to look up.

**Key Elements:**

- **`build_field_map(profile, *, resume_path, cover_path) → dict[str, str]`**
  - Returns:
    ```python
    {
        "full_name": "Casey Hsu",
        "first_name": "Casey",
        "last_name": "Hsu",
        "email": "...",
        "phone": "...",
        "linkedin": "...",
        "github": "...",
        "portfolio": "...",
        "website": "...",
        "city": "Toronto",
        "region": "Ontario",
        "country": "Canada",
        "work_auth_canada": "Yes" | "No",
        "requires_visa_sponsorship": "Yes" | "No",
        "salary_expectation": "100k–120k",
        "pronouns": "...",
        "resume_path": "/abs/path/to/resume.docx",
        "cover_letter_path": "/abs/path/to/cover-letter.md",
    }
    ```
  - Handlers look up keys like `field_map.get("email")` to auto-fill.

---

### `src/jobhunt/browser/handlers/greenhouse.py` — Greenhouse Handler

**Responsibility:** Detect and fill Greenhouse-hosted application forms (public boards.greenhouse.io).

**Key Elements:**

- **`async greenhouse_fill(page, field_map) → list[FieldFill]`**
  - Direct selectors for known Greenhouse fields: `input#first_name`, `input#last_name`, `input#email`, etc.
  - Resume upload: detects `input[type="file"][name*="resume"]`.
  - Delegates remaining fields to `generic_fill()`.
  - Logs all actions to `FieldFill` list.

---

### `src/jobhunt/browser/handlers/_generic.py` — Generic Fallback Handler

**Responsibility:** Fill common form fields on any ATS (not Greenhouse-specific).

**Strategy:**

- Query selectors for common patterns: `input[name*="email"]`, `textarea[name*="cover"]`, etc.
- Match by name/id/placeholder attributes.
- Fill text values into `<input>` and `<textarea>`.
- Upload files to `<input type="file">`.
- Log actions.

---

### `src/jobhunt/browser/handlers/types.py` — Types

**Key Elements:**

- **`FieldFill` dataclass:**
  - `selector: str` — CSS/XPath selector of the form field.
  - `profile_key: str` — Key from `profile_map`.
  - `value: str` — Value filled.
  - `kind: str` — "fill" | "upload" | "skipped" (default: "fill").
  - `note: str | None` — Error reason if skipped.

---

## Resume Processing

### `src/jobhunt/resume/parse_docx.py` — Parse Baseline Resume

**Responsibility:** Extract structured facts from `Casey_Hsu_Resume_Baseline.docx` into `verified.json` and markdown files.

**Flow:**

```
Casey_Hsu_Resume_Baseline.docx (python-docx)
  ↓
Parse sections:
  - Name (first line)
  - Contact line (email, phone)
  - Summary (paragraph or bullet)
  - Technical Skills (categorized)
  - Professional Experience (roles with bullets)
  - Certifications & Education
  ↓
Emit VerifiedFacts dataclass
  ├─ name, contact_line
  ├─ summary
  ├─ skills_core, skills_cms, skills_data_devops, skills_ai, skills_familiar
  ├─ work_history: list[Role]
  │   └─ Role: title, employer, dates, bullets
  ├─ certifications, education, coursework_baseline
  ↓
Serialize to:
  - kb/profile/verified.json
  - kb/profile/resume.md
  - kb/profile/skills.md
  - kb/profile/work-history.md
  - kb/profile/education.md
```

**Key Elements:**

- **`VerifiedFacts` dataclass:**
  - `name, contact_line, summary: str`
  - `skills_core, skills_cms, skills_data_devops, skills_ai, skills_familiar: list[str]`
  - `work_history: list[Role]`
  - `certifications, education, coursework_baseline: list[str]`

- **`Role` dataclass:**
  - `title, employer, dates: str`
  - `bullets: list[str]`

- **`SECTION_HEADERS` set** — Expected section names (SUMMARY, TECHNICAL SKILLS, etc.).

- **`parse_baseline(docx_path: Path) → VerifiedFacts`**
  - Use `python-docx` to parse document.
  - Extract text by section.
  - Classify skills into categories.
  - Return `VerifiedFacts`.

- **`write_verified_json(facts, path) → None`** — Serialize to JSON.

- **`write_kb_markdown(facts, kb_dir) → list[Path]`** — Write markdown files, return list of written paths.

---

### `src/jobhunt/resume/render_docx.py` — Render Tailored Resume

**Responsibility:** Convert `TailoredResume` → ATS-safe .docx file.

**ATS Rules Enforced:**

- Single column (no tables for layout).
- Calibri font, 10.5pt body, 16pt name.
- Real bullet list style (not typed asterisks).
- US Letter, 0.5–0.75" margins.
- No graphics, photos, headers, footers, or text in headers/footers.
- Metadata scrubbed (clear author, last-modified; set to candidate name).

**Key Elements:**

- **`render(tailored: TailoredResume, contact_line: str, name: str, out_path: Path) → Path`**
  - Create new `Document()`.
  - Set margins, default font, metadata.
  - Add name (16pt bold).
  - Add contact line.
  - Add SUMMARY section.
  - Add TECHNICAL SKILLS (category: items format).
  - Add PROFESSIONAL EXPERIENCE (role | employer, dates right-aligned, bullets).
  - Add CERTIFICATIONS & EDUCATION.
  - Add Coursework (with Dean's List prefix).
  - Save to `out_path`.
  - Return path.

- **`_set_margins(doc) → None`** — Set 0.5–0.75" margins.

- **`_set_default_font(doc) → None`** — Set Calibri 10.5pt for all body text.

- **`_scrub_metadata(doc, name) → None`** — Remove OOXML core properties; set author/lastModifier to candidate name.

- **`_add_name(doc, name) → None`** — Add name in 16pt bold.

- **`_add_section_heading(doc, heading) → None`** — Add section heading in 11pt bold gray.

- **`_add_paragraph(doc, text) → None`** — Add body paragraph.

- **`_add_right_tab_stop(p) → None`** — Add right-aligned tab stop at page margin (for dates).

- **`_tighten(p) → None`** — Reduce paragraph spacing (before/after).

**Output:** Single-page .docx file, ATS-parseable.

---

### `src/jobhunt/resume/render_cover_docx.py` — Render Cover Letter

**Responsibility:** Convert cover letter markdown → formatted .docx (similar ATS rules).

**Key Elements:**

- **`async render_cover(cover_md: str, out_path: Path) → Path`**
  - Parse cover letter markdown (salutation, body paragraphs, sign-off).
  - Create .docx with proper formatting.
  - Apply ATS constraints (Calibri, single column, no graphics).
  - Save and return path.

---

## Gateway (Ollama Integration)

### `src/jobhunt/gateway/client.py` — Ollama Client

**Responsibility:** HTTP client to Ollama `/api/chat` endpoint with JSON-schema-constrained output.

**Key Elements:**

- **`async complete_json(*, base_url, model, system, user, schema, temperature=0.0, num_ctx=6144, timeout_s=180.0, keep_alive="30m") → dict`**
  - POST to Ollama `/api/chat` (infers host from base_url).
  - Payload:
    ```python
    {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": schema,  # JSON-schema constraint
        "think": False,
        "keep_alive": keep_alive,  # Keep model in VRAM across jobs
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    ```
  - Extracts `response.message.content` and parses as JSON.
  - Raises `GatewayError` on HTTP error, timeout, invalid JSON, or non-dict response.
  - Default `keep_alive="30m"` keeps model hot across the scan run (avoids 5-15s reload per job).

---

### `src/jobhunt/gateway/prompts.py` — Prompt Loader

**Responsibility:** Load prompts from markdown files in `kb/prompts/` with frontmatter (schema, temperature, task).

**Format:**

```markdown
---
task: score
temperature: 0.0
schema:
  type: object
  properties:
    score: { type: integer, minimum: 0, maximum: 100 }
    reasons: { type: array, items: { type: string } }
    # ...
  required: [score, reasons, ...]
---

## SYSTEM

[System prompt text]

## USER

[User template with {variables} to be filled at runtime]
```

**Key Elements:**

- **`Prompt` dataclass:**
  - `name, task, temperature: float`
  - `system, user_template: str`
  - `schema: dict[str, Any]` (JSON-schema)
  - `render_user(**vars) → str` — Substitute variables into template.

- **`load_prompt(kb_dir: Path, name: str) → Prompt`**
  - Load `kb/prompts/{name}.md`.
  - Parse frontmatter (TOML/YAML-ish).
  - Extract ## SYSTEM and ## USER sections.
  - Return `Prompt`.

---

## Database & Migrations

### Database Schema

**File:** `migrations/0001_init.sql`

**Tables:**

1. **`jobs`**
   - `id TEXT PRIMARY KEY` — `{source}:{slug}:{external_id}`
   - `source TEXT` — "greenhouse", "lever", etc.
   - `external_id TEXT` — ID from source API.
   - `company TEXT` — Company slug or name.
   - `title TEXT` — Job title.
   - `location TEXT` — Free-text location.
   - `remote_type TEXT` — "onsite", "hybrid", "remote", "unknown".
   - `description TEXT` — Job description (HTML-stripped).
   - `url TEXT` — Link to application page.
   - `posted_at TIMESTAMP` — Posted date (if available).
   - `ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` — When we fetched it.
   - `raw_json TEXT` — Raw API response (for debugging).
   - **Unique:** (source, external_id) — Prevents duplicates.
   - **Indexes:** source, company, posted_at.

2. **`scores`**
   - `job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE`
   - `score INTEGER` — Fit score (0–100).
   - `reasons TEXT` — JSON array of reasons.
   - `red_flags TEXT` — JSON array of red flags.
   - `must_clarify TEXT` — JSON array of clarifications.
   - `model TEXT` — Model used for scoring.
   - `prompt_hash TEXT` — Hash of prompt (for rescore detection).
   - `scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
   - **Index:** score.

3. **`applications`**
   - `id TEXT PRIMARY KEY` — UUID generated per application.
   - `job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id) ON DELETE CASCADE`
   - `status TEXT DEFAULT 'drafted'` — drafted | applied | interviewing | offer | rejected | withdrawn.
   - `resume_path TEXT` — Path to tailored .docx.
   - `cover_path TEXT` — Path to cover letter.
   - `fill_plan_path TEXT` — Path to fill-plan.json.
   - `applied_at TIMESTAMP` — Set when status transitions to "applied".
   - `notes TEXT` — User notes.
   - **Index:** status.

4. **`migrations` (auto-created by db.py)**
   - `id TEXT PRIMARY KEY` — Migration ID (e.g., "0001_init").
   - `applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

**Additional Migrations:**

- `0002_apply_tracking.sql` — Adds `jobs.decline_reason` and `applications.applied_week` (ISO week label).
- `0003_outcomes.sql` — Adds `applications.outcome_at` (for interview-rate tracking).

---

## Data Models

### `src/jobhunt/models.py`

**Key Elements:**

- **`RemoteType`** — Literal["onsite", "hybrid", "remote", "unknown"].

- **`ApplicationStatus`** — Literal["drafted", "applied", "interviewing", "offer", "rejected", "withdrawn"].

- **`Job` (Pydantic BaseModel):**
  - `id, source, external_id: str` (primary key, source, remote ID).
  - `company, title, location: str | None`
  - `remote_type: RemoteType`
  - `description: str | None` — Full JD text.
  - `url: str | None` — Application link.
  - `posted_at: datetime | None`
  - `ingested_at: datetime | None`
  - `raw_json: str | None` — Raw API response.

- **`Company` (Pydantic BaseModel):**
  - `name: str`
  - `homepage: str | None`
  - `notes: str | None`

- **`Score` (Pydantic BaseModel):**
  - `job_id, score: str, int`
  - `reasons, red_flags, must_clarify: list[str]`
  - `model, prompt_hash: str | None`
  - `scored_at: datetime | None`

- **`Application` (Pydantic BaseModel):**
  - `id: str`
  - `job_id: str`
  - `status: ApplicationStatus`
  - `resume_path, cover_path, fill_plan_path: str | None`
  - `applied_at: datetime | None`
  - `notes: str | None`

---

## Error Handling

### `src/jobhunt/errors.py`

**Exception Hierarchy:**

```python
JobHuntError (base)
├── ConfigError — Config loading / validation.
├── MigrationError — DB migration failures.
├── IngestError — Job ingestion failures.
├── GatewayError — Ollama communication failures.
├── PipelineError — Scoring, tailoring, cover generation failures.
└── BrowserError — Playwright automation failures.
```

**All CLI commands catch `JobHuntError` and exit with code 1.** No uncaught exceptions leak to stderr (unless `--debug`).

---

## Configuration Files

### `~/.config/jobhunt/config.toml`

**Example:**

```toml
[paths]
data_dir = "data"
db_path = "data/jobhunt.db"
migrations_dir = "migrations"
kb_dir = "kb"

[ingest]
user_agent = "job-seeker/0.1 (+personal-use; caseyhsu@proton.me)"
rate_limit_per_sec = 1.0
greenhouse = ["stripe", "shopify", "1password"]
lever = ["benchsci", "ada"]
ashby = ["ramp", "linear"]
smartrecruiters = ["company-slug"]
workday = ["rbc:wd3:RBC_Careers", "td:wd5:TD_Careers"]
job_bank_ca = []
rss = ["https://company.com/careers/feed.xml"]

[ingest.adzuna]
queries = ["javascript developer", "react developer", ...]
pages = 3
results_per_page = 50

[gateway]
base_url = "http://localhost:11434/v1"
api_key = "ollama"

[gateway.tasks]
score = "qwen3.5:9b"
tailor = "qwen3.5:9b"
cover = "qwen3.5:9b"
qa = "qwen3.5:9b"
embed = "nomic-embed-text"

[pipeline]
score_concurrency = 2
tailor_max_words = 700
cover_max_words = 280
cover_retry_attempts = 3
min_score = 65

[browser]
headed = true
user_data_dir = "data/browser-profile"

[applicant]
full_name = "Casey Hsu"
email = "casey-hsu@outlook.com"
phone = "(416) 555-0123"
linkedin_url = "https://linkedin.com/in/casey-hsu"
github_url = "https://github.com/SimBuds"
portfolio_url = "https://caseyhsu.com"
city = "Toronto"
region = "Ontario"
country = "Canada"
work_auth_canada = true
requires_visa_sponsorship = false
salary_expectation_cad = "100k–120k"
pronouns = ""
```

### `~/.config/jobhunt/secrets.toml`

**File Mode:** 0600 (readable only by owner).

**Example:**

```toml
adzuna_app_id = "your_app_id"
adzuna_app_key = "your_app_key"
```

**Alternative:** Set environment variables:

```bash
export JOBHUNT_ADZUNA_APP_ID="..."
export JOBHUNT_ADZUNA_APP_KEY="..."
```

---

## Knowledge Base

### `kb/profile/verified.json`

**Generated by:** `convert_resume_cmd`.

**Content:**

```json
{
  "name": "Casey Hsu",
  "contact_line": "casey-hsu@outlook.com | (416) 555-0123",
  "summary": "...",
  "skills_core": ["JavaScript", "TypeScript", "React", ...],
  "skills_cms": ["Shopify (Liquid, custom themes)", "HubSpot CMS (HubL, CRM integration)", ...],
  "skills_data_devops": ["MongoDB", "MySQL", "PostgreSQL", "Docker", ...],
  "skills_ai": ["local LLM hosting (Ollama, GPU optimization)", "prompt engineering", ...],
  "skills_familiar": ["Java", "Python", "Agile/Scrum", ...],
  "work_history": [
    {
      "title": "Web Developer (Contract)",
      "employer": "Custom Jewelry Brand (NDA)",
      "dates": "2023 – Present",
      "bullets": ["..."]
    },
    ...
  ],
  "certifications": ["Contentful Certified Professional + Personalization Skill Badge (October 2025)"],
  "education": ["Computer Programming & Analysis (Advanced Diploma), George Brown College (April 2024). Dean's List, all terms."],
  "coursework_baseline": ["Introduction to Web Development", "Advanced Web Programming", ...]
}
```

### `kb/prompts/score.md`

**Format:** Markdown with TOML frontmatter.

**Frontmatter:**

```yaml
---
task: score
temperature: 0.0
schema:
  type: object
  properties:
    score:
      type: integer
      minimum: 0
      maximum: 100
    # ... (full schema)
  required: [score, ...]
---
```

**Sections:**

- `## SYSTEM` — System prompt (instructions for the model).
- `## USER` — User template with `{verified_facts}`, `{policy}`, `{title}`, `{company}`, `{location}`, `{description}` placeholders.

### `kb/prompts/tailor.md`

**Similar structure; temperature 0.3 (lower than cover for determinism).**

**Constraints enforced in prompt + post-decode by `_enforce_no_fabrication()`.**

### `kb/prompts/cover.md`

**Temperature 0.7 (higher for creative latitude).**

**Validates against `cover_validate.py` rules; retries up to 3x on violations.**

### `kb/policies/tailoring-rules.md`

**Prompt-injectable mirror of `Resume_Tailoring_Instructions.md`.**

**Used in score prompt to guide must-have extraction and auto-decline logic.**

---

## Testing & Test Fixtures

### Test Files

**Location:** `tests/`

**Key Test Modules:**

- `test_db.py` — SQLite connection, migrations, upsert helpers.
- `test_gta_filter.py` — Location filtering logic.
- `test_ingest_adapters.py` — Parse sample API responses without network.
- `test_parse_docx.py` — Resume parsing.
- `test_render_docx.py` — .docx rendering.
- `test_score_clamp.py` — Score clamping logic.
- `test_tailor_invariants.py` — `_enforce_no_fabrication()` checks.
- `test_cover_validate.py` — Cover letter validation.
- `test_audit.py` — Audit determinism.
- `test_apply_picks.py` — Selection mode parsing.
- `test_config.py` — Config loading.
- `test_gateway_errors.py` — Ollama error handling.

### Test Fixtures

**Location:** `tests/fixtures/`

**Samples:**

- `greenhouse.json` — Sample Greenhouse API response.
- `lever.json` — Sample Lever API response.
- `ashby.json` — Sample Ashby API response.
- `smartrecruiters.json` — Sample SmartRecruiters API response.
- `job_bank_ca.xml` — Sample Job Bank RSS feed.
- `rss_generic.xml` — Sample generic RSS feed.
- `workday.json` — Sample Workday CXS response.

### CI/CD Rules

- **`pytest -q`** — Unit tests only; no live HTTP or Ollama calls.
- **Manual integration tests** — `scan` with real Ollama, real job ingestion, pipeline end-to-end.
- **Manual browser tests** — `apply` with Playwright (headed, human-reviewed).

---

## Development Workflow

### Commands for Day-to-Day

```bash
# Setup (first time)
uv sync
uv run playwright install chromium
uv run job-seeker config show          # Writes default config
uv run job-seeker db init              # Runs migrations
uv run job-seeker convert-resume       # Parse baseline.docx

# Daily use
uv run job-seeker scan                 # Ingest + score
uv run job-seeker list --min-score 70  # View high-fit jobs
uv run job-seeker apply --best         # Interactive picker + autofill

# Debugging
uv run job-seeker --debug apply --no-browser <job-id>  # Full traceback, no browser
uv run job-seeker -v scan              # Verbose logging
```

### Linting & Type Checking

```bash
uv run ruff check src tests
uv run mypy --strict src/
```

---

## Deployment Notes

### Single Hot Model Strategy

All tasks (score, tailor, cover, qa) use **`qwen3.5:9b`** as the single hot model. This eliminates reload churn:

- **Old strategy** (May 2026 before): 8B for scoring, 14B for generation → 5-15s reload between each task.
- **Current strategy** (May 2026 onward): Single 9B model across all tasks → No reload, deterministic post-processing (score clamp, cover validator) maintains quality.

### Hardware Requirements

- Ollama: 10 GB VRAM (RTX 3080). Arch desktop idles around 1.5 GB GPU so the
  full 10 GB is available to Ollama. `OLLAMA_GPU_OVERHEAD` is intentionally
  unset — qwen3.5:9b at `num_ctx=6144` lands at ~9.1 GB resident with headroom.
- System RAM: 32 GB (browser, SQLite cache, model offloads).
- Disk: ~20 GB for models; local data in `data/`.

### Environment Setup

```bash
ollama serve                   # start Ollama daemon
ollama pull qwen3.5:9b         # single hot model for all task slots
ollama pull nomic-embed-text   # reserved for future kb retrieval
```

---

## Summary

This document serves as the **single source of truth** for architecture and implementation details. Every module, function, and responsibility is outlined. For questions not answered here, consult the docstrings in the code or the CLAUDE.md guardrails file.
