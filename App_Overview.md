# jobhunt — Master Function & Architecture Reference

> Low-level map of every module, class, function, and method in the codebase.
> Use this to navigate quickly, understand call chains, and find the right place for new code.

---

## Table of Contents

1. [Entry Points & CLI](#1-entry-points--cli)
2. [Configuration & Secrets](#2-configuration--secrets)
3. [Domain Models](#3-domain-models)
4. [Errors](#4-errors)
5. [Database Layer](#5-database-layer)
6. [HTTP Utilities](#6-http-utilities)
7. [Gateway (LLM)](#7-gateway-llm)
8. [Pipeline](#8-pipeline)
9. [Ingestion Adapters](#9-ingestion-adapters)
10. [Resume Parser & Renderer](#10-resume-parser--renderer)
11. [Browser Autofill](#11-browser-autofill)
12. [Commands](#12-commands)
13. [Knowledge Base Prompts](#13-knowledge-base-prompts)
14. [Database Migrations](#14-database-migrations)
15. [Tests](#15-tests)
16. [Call-Chain Summaries](#16-call-chain-summaries)

---

## 1. Entry Points & CLI

### `src/jobhunt/__init__.py`
| Symbol | Kind | Notes |
|---|---|---|
| `__version__` | `str` | `"0.1.0"` |

### `src/jobhunt/cli.py`
| Symbol | Signature | Notes |
|---|---|---|
| `app` | `typer.Typer` | Root app; subcommands wired in from each `commands/` module |
| `main` | `(ctx, debug, verbose) -> None` | Typer callback; sets up structlog level |
| `_run` | `() -> None` | `app()` wrapper used as `[project.scripts]` entry point |

**Subcommand wiring order:** `convert_resume_cmd.app`, `scan_cmd.app`, `apply_cmd.app`, `list_cmd.app`, `db_cmd.app` (hidden), `config_cmd.app` (hidden).

---

## 2. Configuration & Secrets

### `src/jobhunt/config.py`

#### Pydantic models (schema-validated config tree)
| Model | Key Fields |
|---|---|
| `PathsConfig` | `data_dir`, `db_path`, `migrations_dir`, `kb_dir` |
| `AdzunaConfig` | `queries: list[str]`, `pages: int`, `results_per_page: int` |
| `IngestConfig` | `user_agent`, `rate_limit_per_sec`, `cache_ttl_hours`, `greenhouse: list[str]`, `lever: list[str]`, `ashby: list[str]`, `smartrecruiters: list[str]`, `workday: list[str]`, `job_bank_ca: list[str]`, `rss: list[str]`, `adzuna: AdzunaConfig` |
| `GatewayConfig` | `base_url`, `api_key`, `tasks: dict[str, TaskConfig]` |
| `PipelineConfig` | `score_concurrency`, `tailor_max_words`, `cover_max_words`, `cover_retry_attempts`, `min_score` |
| `BrowserConfig` | `headed`, `user_data_dir` |
| `ApplicantProfile` | `full_name`, `email`, `phone`, `linkedin_url`, `github_url`, `portfolio_url`, `city`, `region`, `country`, `work_auth_canada`, `requires_visa_sponsorship`, `salary_expectation_cad`, `pronouns` |
| `Config` | `paths`, `ingest`, `gateway`, `pipeline`, `browser`, `applicant` |

#### Standalone functions
| Function | Signature | Notes |
|---|---|---|
| `_default_config_path` | `() -> Path` | `~/.config/jobhunt/config.toml` |
| `_default_data_dir` | `() -> Path` | `~/.local/share/jobhunt` (XDG) |
| `_to_toml_dict` | `(obj: Any) -> dict` | Recurses Pydantic/dataclass → plain dict for TOML serialization |
| `_apply_env_overrides` | `(data: dict) -> dict` | Applies `JOBHUNT_*` env vars with `__` nesting separator |
| `load_config` | `(path: Path \| None, *, write_default_if_missing: bool) -> Config` | Main loader; writes default if absent |
| `config_path` | `() -> Path` | Returns resolved config file path |

**Constants:** `ENV_PREFIX = "JOBHUNT_"`, `ENV_NESTED_SEP = "__"`

### `src/jobhunt/secrets.py`

| Symbol | Signature | Notes |
|---|---|---|
| `Secrets` | Pydantic model | `adzuna_app_id`, `adzuna_app_key` |
| `secrets_path` | `() -> Path` | `~/.config/jobhunt/secrets.toml` (mode 0600) |
| `load_secrets` | `() -> Secrets` | Raises `ConfigError` if file missing |

---

## 3. Domain Models

### `src/jobhunt/models.py`

| Symbol | Kind | Fields / Notes |
|---|---|---|
| `RemoteType` | type alias | `Literal["onsite","hybrid","remote","unknown"]` |
| `ApplicationStatus` | type alias | `Literal["drafted","applied","interviewing","offer","rejected","withdrawn"]` |
| `Job` | Pydantic | `id`, `source`, `external_id`, `company`, `title`, `location`, `remote_type`, `description`, `url`, `posted_at`, `ingested_at`, `raw_json` |
| `Company` | Pydantic | `name`, `homepage`, `notes` |
| `Score` | Pydantic | `job_id`, `score`, `reasons`, `red_flags`, `must_clarify`, `model`, `prompt_hash`, `scored_at` |
| `Application` | Pydantic | `id`, `job_id`, `status`, `resume_path`, `cover_path`, `fill_plan_path`, `applied_at`, `notes` |

---

## 4. Errors

### `src/jobhunt/errors.py`

| Exception | Parent | When raised |
|---|---|---|
| `JobHuntError` | `Exception` | Base; never raised directly |
| `ConfigError` | `JobHuntError` | Bad/missing config or secrets |
| `MigrationError` | `JobHuntError` | SQL migration failure |
| `IngestError` | `JobHuntError` | Ingest adapter failures |
| `GatewayError` | `JobHuntError` | Ollama call errors (timeout, parse, HTTP) |
| `PipelineError` | `JobHuntError` | Score / tailor / cover logic failures |
| `BrowserError` | `JobHuntError` | Playwright failures |

---

## 5. Database Layer

### `src/jobhunt/db.py`

#### Dataclasses
| Symbol | Fields |
|---|---|
| `MigrationResult` | `applied: int`, `skipped: int` |

#### Functions
| Function | Signature | Notes |
|---|---|---|
| `connect` | `(db_path: Path) -> sqlite3.Connection` | `PRAGMA journal_mode=WAL`, `row_factory=sqlite3.Row` |
| `_ensure_migrations_table` | `(conn) -> None` | Creates `schema_migrations` table if absent |
| `migrate` | `(conn, migrations_dir: Path) -> MigrationResult` | Runs numbered `.sql` files in order; idempotent |
| `upsert_job` | `(conn, job: Job) -> bool` | `INSERT OR IGNORE`; returns True if newly inserted |
| `unscored_jobs` | `(conn, limit: int \| None) -> list[Row]` | Jobs with no score row |
| `jobs_to_score` | `(conn, *, current_hash: str, limit: int \| None) -> list[Row]` | New jobs + jobs scored with stale prompt hash |
| `upsert_application` | `(conn, *, application_id, job_id, status, resume_path, cover_path, fill_plan_path, applied_week, notes) -> None` | Insert or replace application row |
| `set_decline_reason` | `(conn, job_id: str, reason: str \| None) -> None` | Updates `jobs.decline_reason` |
| `write_score` | `(conn, *, job_id, score, reasons, red_flags, must_clarify, model, prompt_hash) -> None` | Upserts score row |

**Constants:** `MIGRATION_FILE_RE`, `_TERMINAL_STATUSES`

---

## 6. HTTP Utilities

### `src/jobhunt/http.py`

#### Classes
| Class | Method | Signature | Notes |
|---|---|---|---|
| `RateLimiter` | `__init__` | `(rate_per_sec: float) -> None` | Per-host token bucket |
| `RateLimiter` | `wait` | `async (host: str) -> None` | Sleeps to enforce rate limit |

#### Functions
| Function | Signature | Notes |
|---|---|---|
| `host_of` | `(url: str) -> str` | `urllib.parse.urlparse` hostname extraction |
| `get_json` | `async (client, url, limiter, *, params, max_retries=3) -> Any` | Exponential backoff on 429/5xx |
| `post_json` | `async (client, url, limiter, *, json_body, max_retries=3) -> Any` | Same backoff logic |
| `resolve_redirect` | `async (client, url, limiter, *, max_hops=5) -> str` | HEAD → GET fallback; loop detection |
| `with_client` | `async (fn, *, user_agent=DEFAULT_UA) -> T` | Manages `httpx.AsyncClient` lifecycle |

**Constants:** `DEFAULT_UA = "job-seeker/0.1 (+personal-use; caseyhsu@proton.me)"`

---

## 7. Gateway (LLM)

### `src/jobhunt/gateway/client.py`

| Function | Signature | Notes |
|---|---|---|
| `complete_json` | `async (*, base_url, model, system, user, schema, temperature=0.0, num_ctx=6144, timeout_s=180.0, keep_alive="30m") -> dict` | POSTs to Ollama `/api/chat` with `format=<schema>`; retries on empty response; raises `GatewayError` |
| `_post` _(nested)_ | `async (p: dict) -> str` | Inner single-attempt POST; unwraps `message.content` |

**Behavior:** Sends `"think": false` to suppress qwen3.5 reasoning tokens. `keep_alive="30m"` keeps model resident across scans.

### `src/jobhunt/gateway/prompts.py`

#### Dataclasses
| Symbol | Fields | Notes |
|---|---|---|
| `Prompt` | `name`, `task`, `temperature`, `system`, `user_template`, `schema` | Loaded from frontmatter-annotated markdown |
| `Prompt.render_user` | `(**vars: Any) -> str` | Formats `user_template` with supplied variables |

#### Functions
| Function | Signature | Notes |
|---|---|---|
| `load_prompt` | `(kb_dir: Path, name: str) -> Prompt` | Reads `kb/prompts/<name>.md`; parses YAML frontmatter; raises `ConfigError` if missing |

### `src/jobhunt/gateway/__init__.py`
Re-exports: `complete_json`, `Prompt`, `load_prompt`.

---

## 8. Pipeline

### `src/jobhunt/pipeline/_keywords.py`

| Function | Signature | Notes |
|---|---|---|
| `phrase_tokens` | `(phrase: str) -> list[str]` | Lowercases, tokenizes on `[a-z0-9+#./-]+`, strips stopwords |
| `phrase_present` | `(phrase: str, blob: str) -> bool` | All tokens of phrase must appear in blob (order-insensitive) |

**Constants:** `_TOKEN_RE`, `_STOPWORDS` (common English words excluded from keyword matching)

---

### `src/jobhunt/pipeline/score.py`

#### Dataclasses
| Symbol | Fields |
|---|---|
| `ScoreResult` | `score: int`, `matched_must_haves: list[str]`, `gaps: list[str]`, `decline_reason: str \| None`, `ai_bonus_present: bool`, `model: str` |

#### Functions
| Function | Signature | Notes |
|---|---|---|
| `truncate` | `(s: str, limit: int) -> str` | Hard character truncation with `…` suffix |
| `score_job` | `async (cfg: Config, job: Job) -> ScoreResult` | Main entry: loads prompt, calls gateway, coerces, clamps, checks bogus-senior, verifies against profile |
| `_coerce_phrase_list` | `(raw: object) -> list[str]` | Normalizes LLM output to `list[str]` regardless of format |
| `_is_bogus_senior_decline` | `(decline_reason: str \| None, title: str) -> bool` | Returns True if only "Senior" seniority in title drove decline (not Lead/Principal/Architect/Staff) |
| `_verify_against_profile` | `(llm_matched, llm_gaps, verified_blob) -> tuple[list[str], list[str]]` | Cross-checks LLM phrases against `verified.json`; demotes false-positives back to gaps |
| `_coverage_pct` | `(matched, gaps) -> int` | `len(matched) / (len(matched)+len(gaps)) * 100` rounded |
| `_clamp_by_coverage` | `(raw_score, coverage_pct) -> int` | Clamps LLM score down if coverage is low |
| `prompt_hash` | `(kb_dir: Path) -> str` | SHA-256 of `score.md` content; used to detect stale scores |

**Constants:** `MAX_DESC_CHARS = 6000`, `MAX_POLICY_CHARS = 4000`

---

### `src/jobhunt/pipeline/tailor.py`

#### Dataclasses
| Symbol | Fields |
|---|---|
| `TailoredCategory` | `name: str`, `items: list[str]` |
| `TailoredRole` | `title`, `employer`, `dates`, `bullets: list[str]` |
| `TailoredResume` | `summary`, `skills_categories: list[TailoredCategory]`, `roles: list[TailoredRole]`, `certifications`, `education`, `coursework`, `model` |

#### Functions
| Function | Signature | Notes |
|---|---|---|
| `tailor_resume` | `async (cfg: Config, job: Job) -> TailoredResume` | Loads `tailor` prompt, calls gateway, parses, normalizes, enforces no-fabrication |
| `_complete_familiar_bucket` | `(tailored, verified) -> None` | Fills Familiar skill bucket from `verified.json` if under `_FAMILIAR_FLOOR=4` |
| `_dedupe_education` | `(tailored) -> None` | Removes Dean's List / coursework lines from Education (they live in dedicated fields) |
| `_shrink_to_one_page` | `(tailored) -> None` | Trims Familiar items → role bullets → whole roles until `fits_one_page` is True |
| `_normalize_aliases` | `(raw: dict) -> dict` | Handles LLM key variants (e.g. `work_experience` → `roles`) |
| `_parse` | `(raw: dict, model: str) -> TailoredResume` | Validates + constructs dataclass from LLM JSON |
| `_tokens` | `(s: str) -> frozenset[str]` | Word tokens for fuzzy matching |
| `_has_word` | `(text: str, word: str) -> bool` | Whole-word regex match |
| `_check_summary` | `(summary: str, verified: dict) -> None` | Raises `PipelineError` if summary opens with culinary term or uses unverified seniority |
| `_enforce_no_fabrication` | `(tailored, verified) -> None` | Checks every role/employer/dates, every skill, and all categories against `verified.json`; raises `PipelineError` on violation |

**Constants:** `_FAMILIAR_FLOOR=4`, `_TOKEN_RE`, `_FORBIDDEN_SENIORITY`, `_CULINARY_TERMS`

---

### `src/jobhunt/pipeline/cover.py`

#### Dataclasses
| Symbol | Fields | Methods |
|---|---|---|
| `CoverLetter` | `salutation`, `body: list[str]`, `sign_off`, `model` | `to_markdown() -> str` — joins salutation + body paragraphs + sign_off |

#### Functions
| Function | Signature | Notes |
|---|---|---|
| `write_cover` | `async (cfg: Config, job: Job, *, revisions: str = "") -> CoverLetter` | Thin wrapper; builds user prompt with optional revision hint, calls `write_cover_with_retry` |
| `write_cover_with_retry` | `async (cfg, job, *, verified, company, max_words, max_attempts) -> tuple[CoverLetter, list[str], int]` | Retry loop: calls gateway → validates → if violations, re-calls with `_format_revision_hint`; returns `(cover, violations, attempts)` |
| `_format_revision_hint` | `(violations: list[str], attempt: int) -> str` | Formats violation list as a `[REVISION REQUIRED]` block for the next prompt |

---

### `src/jobhunt/pipeline/cover_validate.py`

#### Functions
| Function | Signature | Notes |
|---|---|---|
| `_body_text` | `(cover: CoverLetter) -> str` | Joins body paragraphs only |
| `_full_text` | `(cover: CoverLetter) -> str` | Salutation + body + sign-off |
| `_word_count` | `(text: str) -> int` | `\b\w+\b` count |
| `_verified_skill_blob` | `(verified: dict) -> str` | Flattens all skill lists to space-joined string |
| `_verified_numbers` | `(verified: dict) -> set[str]` | Extracts all digit clusters from verified facts |
| `validate_cover` | `(cover, *, verified, company, max_words) -> list[str]` | Master validator; returns list of violation strings (empty = clean) |

**What `validate_cover` checks:**
1. Banned phrases (obsequious, filler, form-letter openers)
2. Exclamation marks
3. Banned openers (leading filler lines)
4. Diploma recap in closing
5. Unfilled `[PLACEHOLDER]` tokens
6. Unverified numbers (not in `verified.json`)
7. Word count > `max_words`
8. Fewer than 3 paragraphs
9. Company name missing from lead paragraph

**Constants:** `BANNED_PHRASES`, `BANNED_OPENERS`, `_FABRICATION_WATCHLIST`, regex patterns for filler/sign-off detection

---

### `src/jobhunt/pipeline/audit.py`

#### Dataclasses
| Symbol | Fields | Methods |
|---|---|---|
| `AuditResult` | `keyword_coverage_pct: int \| None`, `matched_keywords: list[str]`, `missing_must_haves: list[str]`, `fabrication_flags: list[str]`, `cover_letter_violations: list[str]`, `verdict: Literal["ship","revise","block"]` | `to_json() -> str` |

#### Functions
| Function | Signature | Notes |
|---|---|---|
| `_resume_text` | `(tailored: TailoredResume) -> str` | Flattens all resume fields to single string for keyword search |
| `keyword_coverage` | `(must_haves: list[str], tailored: TailoredResume) -> tuple[int \| None, list[str], list[str]]` | Returns `(coverage_pct, matched, missing)`; `None` pct if `must_haves` empty |
| `audit` | `(*, tailored, cover, score, verified, company, cover_max_words) -> AuditResult` | Orchestrates all checks: keyword coverage → cover validate → fabrication re-check → determines verdict |
| `write_audit` | `(out_dir: Path, result: AuditResult) -> Path` | Writes `audit.json` to `data/applications/<id>/`; returns path |

**Verdict rules:**
- `block` — any fabrication flag
- `revise` — keyword coverage < 70% OR cover violations
- `ship` — all clear

**Constants:** `MIN_KEYWORD_COVERAGE_PCT = 70`

---

## 9. Ingestion Adapters

All adapters expose `async def fetch(client, limiter, ...)` and yield `Job` objects. GTA filter (`_filter.is_gta_eligible`) is applied before yielding.

### `src/jobhunt/ingest/_filter.py`
| Function | Signature | Notes |
|---|---|---|
| `is_gta_eligible` | `(location: str) -> bool` | Checks GTA city allowlist + "remote" + Canada heuristics |
| `classify_remote_type` | `(location: str, extra: str) -> RemoteType` | Detects onsite/hybrid/remote/unknown |

### `src/jobhunt/ingest/_rss.py`
| Symbol | Signature | Notes |
|---|---|---|
| `RSSItem` | dataclass | `title`, `link`, `description`, `pub_date`, `guid` |
| `strip_html` | `(text: str) -> str` | Removes tags, normalizes whitespace |
| `_parse_dt` | `(s: str) -> datetime \| None` | RFC 2822 or ISO datetime |
| `parse_feed` | `(xml_text: str) -> Iterator[RSSItem]` | RSS 2.0 + Atom 1.0 parser (no lxml dependency) |
| `fetch_feed` | `async (client, url, limiter, max_retries) -> str` | GET with exponential backoff |

### `src/jobhunt/ingest/greenhouse.py`
| Function | Notes |
|---|---|
| `fetch(client, limiter, slug)` | `GET /boards/<slug>/jobs?content=true` |
| `_strip_html(s)` | HTML → plain text |
| `_parse_dt(s)` | ISO datetime |

### `src/jobhunt/ingest/lever.py`
| Function | Notes |
|---|---|
| `fetch(client, limiter, slug)` | `GET api.lever.co/v0/postings/<slug>?mode=json` |
| `_from_ms(ms)` | ms epoch → datetime |

### `src/jobhunt/ingest/ashby.py`
| Function | Notes |
|---|---|
| `fetch(client, limiter, slug)` | Ashby posting API |
| `_parse_dt(s)` | ISO datetime |

### `src/jobhunt/ingest/smartrecruiters.py`
| Function | Notes |
|---|---|
| `fetch(client, limiter, slug)` | `GET api.smartrecruiters.com/v1/companies/<slug>/postings` with pagination |
| `_format_location(loc)` | Dict → location string + remote flag |
| `_extract_description(j)` | Joins `jobAd.sections` into plain text |
| `_parse_dt(s)` | ISO datetime |

### `src/jobhunt/ingest/job_bank_ca.py`
| Function | Notes |
|---|---|
| `fetch(client, limiter, feed_url)` | Fetches Job Bank Canada RSS |
| `_split_title(raw)` | Parses `"title - employer - location"` format |

### `src/jobhunt/ingest/rss_generic.py`
| Function | Notes |
|---|---|
| `fetch(client, limiter, feed_url)` | Generic employer career RSS/Atom; delegates to `_rss.fetch_feed` + `parse_feed` |

### `src/jobhunt/ingest/adzuna_ca.py`
| Function | Notes |
|---|---|
| `fetch(client, limiter, app_id, app_key, query, pages, results_per_page)` | Adzuna Canada REST API; resolves redirect URLs |
| `_parse_dt(s)` | ISO datetime |

### `src/jobhunt/ingest/workday.py`
| Function | Notes |
|---|---|
| `fetch(client, limiter, spec, max_pages)` | Workday CXS endpoint with pagination |
| `_parse_tenant(spec)` | Parses `"tenant:host:site"` config string |
| `_location_text(item)` | Extracts location from Workday item (list or string) |

---

## 10. Resume Parser & Renderer

### `src/jobhunt/resume/parse_docx.py`

#### Dataclasses
| Symbol | Fields |
|---|---|
| `Role` | `title`, `employer`, `dates`, `bullets: list[str]` |
| `VerifiedFacts` | `name`, `contact_line`, `summary`, `skills_core`, `skills_cms`, `skills_data_devops`, `skills_ai`, `skills_familiar`, `work_history: list[Role]`, `certifications`, `education`, `coursework_baseline` |

#### Functions
| Function | Signature | Notes |
|---|---|---|
| `_split_skills` | `(value: str) -> list[str]` | Comma-split respecting parentheses |
| `parse_baseline` | `(docx_path: Path) -> VerifiedFacts` | Parses `Casey_Hsu_Resume_Baseline.docx` into structured facts |
| `write_verified_json` | `(facts, out_path) -> None` | Serializes to `kb/profile/verified.json` |
| `_md_bullets` | `(items: list[str]) -> str` | `- item\n` list |
| `write_kb_markdown` | `(facts, kb_dir) -> list[Path]` | Generates 4 profile markdown files under `kb/profile/` |

**Constants:** `SECTION_HEADERS`, `_SKILL_LINE_RE`, `_ROLE_LINE_RE`

### `src/jobhunt/resume/render_docx.py`

| Function | Signature | Notes |
|---|---|---|
| `render` | `(tailored, contact_line, name, out_path) -> None` | Main entry; builds ATS-safe .docx (Calibri 10.5pt, single column, real bullets) |
| `_scrub_metadata` | `(doc, name) -> None` | Strips OOXML author metadata |
| `_set_margins` | `(doc) -> None` | 0.5"/0.75" US Letter margins |
| `_set_default_font` | `(doc) -> None` | Normal style → Calibri 10.5pt |
| `_add_name` | `(doc, name) -> None` | Centered heading |
| `_add_contact` | `(doc, contact_line) -> None` | Centered contact row |
| `_add_section_heading` | `(doc, text) -> None` | Bold + bottom border |
| `_add_paragraph` | `(doc, text) -> None` | Standard body paragraph |
| `_tighten` | `(paragraph, before, after) -> None` | Paragraph spacing + line height |
| `_add_right_tab_stop` | `(paragraph) -> None` | 7.0" right-aligned tab |
| `_add_bottom_border` | `(paragraph) -> None` | Gray border under heading |
| `_wrapped_lines` | `(text: str, width: int) -> int` | Estimates line count with wrapping |
| `estimate_lines` | `(tailored: TailoredResume) -> int` | Total estimated lines for one-page check |
| `fits_one_page` | `(tailored: TailoredResume) -> bool` | True if estimated lines ≤ 48 (US Letter budget) |

### `src/jobhunt/resume/render_cover_docx.py`

| Function | Signature | Notes |
|---|---|---|
| `_add_letter_paragraph` | `(doc, text, after) -> None` | Full-gap paragraph, no indent |
| `render_cover` | `(cover: CoverLetter, contact_line, name, out_path) -> None` | Renders cover letter .docx matching resume styling |

---

## 11. Browser Autofill

### `src/jobhunt/browser/profile_map.py`

| Function | Signature | Notes |
|---|---|---|
| `build_field_map` | `(profile: ApplicantProfile, resume_path, cover_path) -> dict[str, str]` | Flat key→value map used by autofill handlers |

### `src/jobhunt/browser/autofill.py`

| Function | Signature | Notes |
|---|---|---|
| `looks_like_application_page` | `async (page) -> bool` | Heuristically detects application form (file input, autocomplete hints, textarea) |
| `autofill` | `async (url, profile, resume_path, cover_path, out_dir, headed, user_data_dir) -> None` | Launches Playwright, navigates, picks handler via `pick_handler(url)`, writes `fill-plan.json`, executes fills |

### `src/jobhunt/browser/handlers/__init__.py`

| Function | Signature | Notes |
|---|---|---|
| `pick_handler` | `(url: str) -> tuple[str, Callable]` | Returns `(name, handler_fn)` based on domain; falls back to generic |

### `src/jobhunt/browser/handlers/types.py`

| Symbol | Fields | Notes |
|---|---|---|
| `FieldFill` | `selector`, `profile_key`, `value`, `kind: Literal["text","upload","select","skipped"]`, `note` | Single planned fill action |

### `src/jobhunt/browser/handlers/_generic.py`

| Function | Signature | Notes |
|---|---|---|
| `_norm` | `(s: str) -> str` | Lowercase + collapse non-alnum |
| `_match` | `(needle: str, haystacks: list[str]) -> bool` | Substring check across candidates |
| `_selector_for` | `(raw_id, raw_name, tag) -> str` | CSS selector from element attributes |
| `generic_fill` | `async (page, field_map) -> list[FieldFill]` | Enumerates form fields, matches to profile keys, returns fill plan |

### `src/jobhunt/browser/handlers/greenhouse.py`

| Function | Signature | Notes |
|---|---|---|
| `greenhouse_fill` | `async (page, field_map) -> list[FieldFill]` | Greenhouse-specific selectors + resume upload handling |

---

## 12. Commands

### `src/jobhunt/commands/__init__.py`

| Function | Signature | Notes |
|---|---|---|
| `ensure_profile` | `(cfg: Config) -> None` | Exits with friendly message if `kb/profile/verified.json` missing; called at top of `scan`, `list`, `apply` |

### `src/jobhunt/commands/convert_resume_cmd.py`

| Symbol | Notes |
|---|---|
| `app` | `typer.Typer` |
| `run(docx: Path)` | Calls `parse_baseline` → `write_verified_json` → `write_kb_markdown` |

### `src/jobhunt/commands/scan_cmd.py`

| Function | Signature | Notes |
|---|---|---|
| `run(skip_score, skip_ingest, limit)` | CLI entry → `asyncio.run(_run(...))` |
| `_run(cfg, *, skip_score, skip_ingest, limit)` | Orchestrates: warm model → ingest all sources → score new jobs |
| `_warm_model(cfg)` | Fires tiny chat to pre-load model into VRAM before scoring loop |
| `_ingest_all(cfg, conn)` | Streams all configured sources concurrently; deduplicates; upserts |
| `_dedup_key(job)` | Normalizes title+company for cross-source dedup |
| `_safe_stream(source, label, stream, progress, task_id, overall_id)` | Wraps source iterator with error isolation + progress reporting |

**Constants:** `_DEDUP_RE`

### `src/jobhunt/commands/apply_cmd.py`

| Function | Signature | Notes |
|---|---|---|
| `run(job_id, top, best, min_score, no_browser, set_status)` | CLI entry; dispatches to status-set path or apply path |
| `_run_set_status(job_id, status)` | Updates application status directly in DB |
| `_resolve_by_id(conn, job_id)` | Fetches job row by exact ID |
| `_unapplied_top_query(min_score, limit)` | Returns `(sql, params)` for top-N query |
| `_resolve_top_n(conn, *, n, min_score)` | Fetches top N unapplied jobs by score |
| `_resolve_interactive(conn, *, min_score)` | Prints top-10 picker table; reads user input |
| `_parse_picks(raw: str, max_n: int) -> list[int]` | Parses `"1,3,5-7"` style input → list of 1-based indices |
| `_apply_each(cfg, rows, *, no_browser)` | Main apply loop: tailor → cover → audit → render .docx → optional autofill |
| `_load_score(cfg, job_id) -> ScoreResult \| None` | Loads score from DB; reconstructs `ScoreResult` |
| `_row_to_job(row) -> Job` | `sqlite3.Row` → `Job` domain object |
| `_company_slug(company: str \| None) -> str` | Normalizes company name for filesystem use (strips Inc/LLC/Corp/etc.) |
| `_safe_id(s: str) -> str` | Replaces non-alnum with `_` for safe filename component |

**Constants:** `VALID_STATUSES`, `_NON_ALNUM_RE`, `_LEGAL_SUFFIX_RE`, `_MAX_COMPANY_SLUG=40`, `_FS_RE`

### `src/jobhunt/commands/list_cmd.py`

| Function | Signature | Notes |
|---|---|---|
| `run(week, status, min_score, source, limit)` | CLI entry → builds query → renders table |
| `_iso_week_label(weeks_ago: int) -> str` | Returns `"YYYY-Www"` label N weeks ago |
| `_query(conn, *, week_label, status, min_score, source, limit) -> list[Row]` | Dynamic SQL query with optional filters |
| `_render_rows(rows, target_week)` | Rich table output |
| `_render_weekly_footer(conn, week_label)` | Weekly stats summary (applied/interviewed/offered) |

### `src/jobhunt/commands/config_cmd.py`

| Function | Notes |
|---|---|
| `show()` | Prints loaded config as TOML |
| `path()` | Prints config file path |
| `calibrate()` | Prints interview-rate per score band (use after ≥20 applications) |

**Constants:** `BANDS = [(85,101,"85–100"), (75,85,"75–84"), (65,75,"65–74"), (0,65,"< 65")]`, `INTERVIEW_STATUSES`

### `src/jobhunt/commands/db_cmd.py`

| Function | Notes |
|---|---|
| `init()` | Creates DB and runs migrations |
| `migrate_cmd()` | Runs pending migrations only |
| `reset(force: bool)` | Wipes DB + `data/applications/` + `data/cache/` + Playwright profile + `kb/profile/`; requires `--force` |

---

## 13. Knowledge Base Prompts

Location: `kb/prompts/` — markdown files with YAML frontmatter. Loaded by `gateway.prompts.load_prompt`.

| File | Task | Temperature | Purpose |
|---|---|---|---|
| `score.md` | `score` | `0.0` | Score job fit 0–100; identify must-haves, gaps, decline reason, AI bonus; includes peer-tech family definitions and conservative auto-decline rules |
| `tailor.md` | `tailor` | `0.3` | Rewrite resume sections targeting the JD; no-fabrication rules injected via system prompt; outputs structured JSON |
| `cover.md` | `cover` | `0.7` | Write 3–4 paragraph cover letter (~250 words); verified facts only; company name required in lead; banned phrases enforced post-generation |

**Profile files** (read-only at runtime, written by `convert-resume`):
- `kb/profile/verified.json` — canonical facts (source of truth for all fabrication checks)
- `kb/profile/skills.md` — skills summary
- `kb/profile/experience.md` — work history
- `kb/profile/summary.md` — professional summary
- `kb/policies/tailoring-rules.md` — non-negotiable tailoring rules (injected into tailor prompt)

---

## 14. Database Migrations

| File | What It Does |
|---|---|
| `migrations/0001_init.sql` | Creates `companies`, `jobs`, `scores`, `applications` tables; indexes on source, company, posted_at, score, status |
| `migrations/0002_apply_tracking.sql` | Adds `jobs.decline_reason`, `applications.applied_week`; indexes both |
| `migrations/0003_outcomes.sql` | Adds `applications.outcome_at`, `applications.audit_json`; indexes status and outcome_at |

---

## 15. Tests

### Test files and their coverage

| File | What It Covers |
|---|---|
| `test_apply_cmd.py` | `_company_slug`: legal suffix stripping, punctuation collapse, empty/None, length cap, underscore boundary |
| `test_apply_picks.py` | `_parse_picks`: blank, CSV, range, mixed, dedup, max clip, invalid-chunk skipping |
| `test_audit.py` | `audit()`: block on fabrication, revise on cover violation, revise on low coverage, ship; `keyword_coverage()`: all present, empty, partial |
| `test_autofill_detect.py` | `looks_like_application_page`: file input, autocomplete, textarea, search-only rejection; `generic_fill`: dashed selector, no-form guard |
| `test_config.py` | Config write/load, env var override, example TOML parseable, invalid TOML raises |
| `test_cover_retry.py` | `_format_revision_hint`: violation list formatting |
| `test_cover_validate.py` | `validate_cover`: all 9 violation types; clean pass |
| `test_db.py` | Schema, idempotent migrations, missing dir error, unique constraint |
| `test_db_writes.py` | `upsert_job`, `upsert_application`, `set_decline_reason`, `jobs_to_score`, `unscored_jobs` |
| `test_gateway_errors.py` | ReadTimeout/ConnectError/HTTP error message formatting; `keep_alive` in payload |
| `test_gta_filter.py` | `is_gta_eligible`: parameterized GTA + remote cases |
| `test_ingest_adapters.py` | RSS parsing, Job Bank title split, GTA filter per adapter, Workday tenant spec, SmartRecruiters location/description, dedup key generation |
| `test_parse_docx.py` | Round-trip parse, missing file error, `_split_skills` paren-awareness |
| `test_redirect_resolve.py` | Happy-path chain, loop detection, network error, HEAD-405 fallback, hop limit, relative URLs |
| `test_render_cover_docx.py` | `render_cover`: valid .docx output |
| `test_render_docx.py` | `render`: valid .docx; `estimate_lines`/`fits_one_page`; Dean's List formatting |
| `test_score_clamp.py` | `_clamp_by_coverage`, `_coverage_pct`, `_verify_against_profile`: all edge cases |
| `test_senior_decline_filter.py` | `_is_bogus_senior_decline`: senior override, Staff/Principal preservation, leadership preservation, non-senior passthrough, null passthrough |
| `test_tailor_invariants.py` | `_enforce_no_fabrication`: 11 invariant cases; `_dedupe_education`; `_shrink_to_one_page` trim order |

---

## 16. Call-Chain Summaries

### `job-seeker scan`
```
scan_cmd.run()
  └─ _run(cfg)
       ├─ ensure_profile(cfg)
       ├─ _warm_model(cfg)                    → gateway.complete_json (tiny ping)
       ├─ _ingest_all(cfg, conn)
       │    ├─ greenhouse.fetch(...)          → http.get_json
       │    ├─ lever.fetch(...)               → http.get_json
       │    ├─ ashby.fetch(...)               → http.get_json
       │    ├─ smartrecruiters.fetch(...)     → http.get_json
       │    ├─ adzuna_ca.fetch(...)           → http.get_json + resolve_redirect
       │    ├─ job_bank_ca.fetch(...)         → _rss.fetch_feed → parse_feed
       │    ├─ rss_generic.fetch(...)         → _rss.fetch_feed → parse_feed
       │    └─ workday.fetch(...)             → http.get_json
       │         └─ _filter.is_gta_eligible() on each job
       │         └─ db.upsert_job()
       └─ score loop (concurrent)
            └─ pipeline.score.score_job(cfg, job)
                 ├─ gateway.prompts.load_prompt("score")
                 ├─ gateway.client.complete_json(...)
                 ├─ _coerce_phrase_list / _is_bogus_senior_decline
                 ├─ _verify_against_profile
                 ├─ _coverage_pct → _clamp_by_coverage
                 └─ db.write_score()
```

### `job-seeker apply <job-id>`
```
apply_cmd.run()
  └─ ensure_profile(cfg)
  └─ _apply_each(cfg, rows)
       └─ for each job:
            ├─ pipeline.tailor.tailor_resume(cfg, job)
            │    ├─ gateway.prompts.load_prompt("tailor")
            │    ├─ gateway.client.complete_json(...)
            │    ├─ _normalize_aliases → _parse
            │    ├─ _complete_familiar_bucket
            │    ├─ _dedupe_education
            │    ├─ _shrink_to_one_page → resume.render_docx.fits_one_page
            │    └─ _enforce_no_fabrication
            ├─ pipeline.cover.write_cover(cfg, job)
            │    └─ write_cover_with_retry(...)
            │         ├─ gateway.client.complete_json(...)
            │         └─ cover_validate.validate_cover(...)  [retry loop]
            ├─ pipeline.audit.audit(...)
            │    ├─ keyword_coverage(must_haves, tailored)
            │    ├─ cover_validate.validate_cover(cover, ...)
            │    └─ tailor._enforce_no_fabrication(tailored, verified)
            ├─ audit.write_audit(out_dir, result)
            ├─ resume.render_docx.render(tailored, ...)
            ├─ resume.render_cover_docx.render_cover(cover, ...)
            └─ browser.autofill.autofill(...)          [unless --no-browser]
                 ├─ handlers.pick_handler(url)
                 └─ handler(page, field_map)           [greenhouse or generic]
```

### `job-seeker convert-resume`
```
convert_resume_cmd.run()
  ├─ resume.parse_docx.parse_baseline(docx_path) → VerifiedFacts
  ├─ resume.parse_docx.write_verified_json(facts, kb/profile/verified.json)
  └─ resume.parse_docx.write_kb_markdown(facts, kb_dir)
       → kb/profile/skills.md
       → kb/profile/experience.md
       → kb/profile/summary.md
       → kb/profile/contact.md
```

### `job-seeker list`
```
list_cmd.run()
  ├─ ensure_profile(cfg)
  ├─ _iso_week_label(week)
  ├─ _query(conn, ...)
  ├─ _render_rows(rows, ...)
  └─ _render_weekly_footer(conn, week_label)   [if --week flag]
```
