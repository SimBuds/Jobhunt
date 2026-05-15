# AGENTS.md

**Source of truth for agents working in this repo.** Recognized by the
cross-tool `AGENTS.md` convention (Cursor, Aider, Codex, etc.) and pulled into
Claude Code via a small `CLAUDE.md` stub that imports this file. Defines the
architecture, conventions, and non-negotiable guardrails for `jobhunt`.

If anything in `PLAN.md` contradicts this file, **this file wins** — open a PR to reconcile rather than working around it.

## Documentation map

- `AGENTS.md` (this file) — agent guardrails and conventions. The *how*.
- `PLAN.md` — design rationale. The *why* decisions were made.
- `README.md` — install + usage for developers running the app locally.
- `Resume_Tailoring_Instructions.md` — honest-tailoring rules (no fabrication, ATS-safe formatting, auto-decline triggers). Mirrored at `kb/policies/tailoring-rules.md` for prompt injection.
- `kb/README.md` — what lives under `kb/` and how each subdirectory is maintained.
- `kb/seeds/gta-employers.toml` — curated verified ATS slugs imported by `jobhunt config seed --apply`. Edit via `scripts/verify_seeds.py`, never hand-add unverified entries.
- `CLAUDE.md` — tiny stub that `@`-imports this file so Claude Code's auto-load still works. Don't edit it; edit this file.

---

## What this project is

A local-first CLI tool for personal job search automation. Pulls jobs from public ATS APIs, runs fit-scoring and document tailoring against the user's profile using local Ollama models, and assists with form autofill via Playwright (human submits, never the bot).

## Hardware context

- Arch Linux, Ryzen 9 5900, 32GB DDR4, RTX 3080 (10 GB VRAM total). Arch idles around 1.5 GB on the GPU, so `OLLAMA_GPU_OVERHEAD` is intentionally **not** set — the full 10 GB is available to Ollama and the active model lands at ~9.1 GB resident with comfortable headroom.
- Ollama at `http://localhost:11434`
- Default model: `qwen-custom:latest` — a Modelfile-derived `qwen3.5:9b` that bakes in the user's personal prompt stack (persona, formatting, knowledge). The gateway always sends a system message, which overrides the Modelfile SYSTEM for structured tasks, so the persona doesn't bleed into scoring/tailoring/cover outputs. Bare `qwen3.5:9b` is the documented fallback if the custom variant isn't built — same base weights, same VRAM footprint, same quirks. All three task slots (score, tailor, cover) run the same hot model at `num_ctx=16384` (matching `OLLAMA_CONTEXT_LENGTH=16384`) with `keep_alive=-1` (per-call override that pins the model in VRAM during active work; the systemd default `OLLAMA_KEEP_ALIVE=10m` handles idle unload between scans) and reasoning (`think`) disabled at the gateway. `nomic-embed-text` reserved for future embeddings. QA is deliberately deterministic (see `pipeline.audit`) — no LLM QA slot.
- Ollama systemd env (Arch, `sudo systemctl edit ollama.service`):
  ```
  Environment="OLLAMA_KV_CACHE_TYPE=q5_0"      # q5_0 KV cache cuts VRAM ~30% vs default
  Environment="OLLAMA_FLASH_ATTENTION=1"       # required to use a quantized KV cache
  Environment="OLLAMA_NUM_PARALLEL=1"          # single concurrent request — matches our sequential pipeline
  Environment="OLLAMA_CONTEXT_LENGTH=16384"    # 16k context; gateway sends num_ctx=16384 to match
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
│   ├── job_bank_ca.py         # Government of Canada Job Bank RSS
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

User-facing surface is **seven** commands. `db` and `config` are hidden internals
(except `config seed`, which is part of the user-facing onboarding flow).

```
jobhunt convert-resume       # parse baseline .docx → kb/profile/
jobhunt scan                 # ingest GTA jobs + score
jobhunt apply <job-id>       # tailor + cover + autofill (you submit)
jobhunt apply --top N        # auto-pick N best-fit unapplied (1..10)
jobhunt apply --best         # interactive picker over top 10
jobhunt apply --url <URL>    # ad-hoc: fetch one JD, score, tailor; prints `add` suggestion
jobhunt add <URL>            # parse URL → write ATS slug to config.toml
jobhunt list [--week N]      # pipeline view + weekly rollup
jobhunt analyze certs [--top N] [--trend] [--window-days N] [--min-score N]
                             # cert frequency, trends, and fit verdicts
jobhunt discover slugs       # legacy: harvest URLs in jobs DB + probe Greenhouse/Ashby
jobhunt config seed --apply  # import kb/seeds/gta-employers.toml into config
```

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
to 3 candidate slugs, then probes the Greenhouse and Ashby public APIs.
Hits are printed; `--apply` appends them to `config.toml` (after writing a
`.bak`). Misses persist to the `slug_probes` table and are skipped on
subsequent runs unless `--include-cached`. Staffing-agency names are filtered
out at the candidate stage — they never run public ATS boards. Per-host rate
limit + per-company 15 s timeout + bounded concurrency keep wall time
predictable; `--limit 100` is the default run cap.

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

1. **Public APIs only.** Greenhouse `boards-api`, Lever `api.lever.co/v0`, Ashby posting API, Adzuna CA (with API key), SmartRecruiters public Posting API (`api.smartrecruiters.com/v1/companies/{slug}/postings`, no key), Job Bank Canada RSS, generic RSS.
2. **GTA scope.** Filter by GTA city allowlist (Toronto, Mississauga, Brampton, Hamilton, Oakville, Markham, Vaughan, Burlington, Oshawa, Richmond Hill, Pickering, Ajax, Whitby, Milton) **plus Remote-Canada** postings. Adzuna uses `where=Toronto&distance=100&country=ca`. Drop everything else.
3. **No LinkedIn, no Indeed, no Glassdoor scraping**, ever. Even if the user asks. Push back and explain.
4. **Respect `robots.txt`** for any non-API HTTP fetch. The `--url` ad-hoc path checks via stdlib `urllib.robotparser` and accepts `--force-robots` for personal-use override only; this carve-out does **not** apply to `scan` ingest adapters. (this file historically called for `protego`; the project hasn't taken that dep yet — stdlib is the current implementation.)
5. **Rate limits:** 1 req/sec/host default. Exponential backoff on 429/5xx.
6. **User-Agent:** identifies the tool and provides a contact, e.g. `jobhunt/0.1 (+personal-use; your-email@example.com)`. Set via `config.toml` under `[ingest] user_agent`.
7. **Cache** raw responses to `data/cache/` with a TTL; don't re-hit APIs needlessly during dev.
8. **Adzuna queries auto-derive from `verified.json`** when `cfg.ingest.adzuna.queries` is empty. `ingest._query_planner.derive_adzuna_queries` walks `skills_core` / `skills_cms` / `skills_familiar` plus work-history bullets and emits up to 10 role-suffixed queries (capped to keep budget at ~30 API calls/scan with `pages=3`). Umbrella triggers (`cms developer`, `ai engineer`, `seo specialist`) fire on bucket-presence / bullet-token signals. Populated `queries` list bypasses the planner entirely. Adding new skill buckets to verified.json requires extending `_SKILL_QUERIES` or `_CATEGORY_TRIGGERS` to surface them.

## Browser automation rules — non-negotiable

1. **Never click a submit button.** Fill fields, then hand off to the human. The user is in the loop on every application.
2. **Never auto-create accounts** on employer sites. If signup is required, exit and tell the user.
3. **Log a field-fill plan** to `data/applications/<job-id>/fill-plan.json` before executing it, for auditability.
4. **Run headed by default.** Headless only if `--headless` flag and only for dry-runs.
5. **No stored employer credentials.** If a site requires login, the user logs in manually each time.

## LLM call rules

1. **Every structured call uses a JSON schema.** `gateway.client.complete_json(schema=...)` posts to Ollama `/api/chat` with `format: <schema>`. No free-form JSON parsing.
2. **Reasoning disabled.** The gateway sends `"think": false` so qwen3.5's
   reasoning trace doesn't blow past the timeout on structured calls (this
   applies to bare `qwen3.5:9b` and the project default `qwen-custom:latest`,
   which is derived from it). Quality is held by the deterministic
   post-processing layers (score clamp, cover validator + retry, audit), not
   by reasoning tokens. If a future task slot needs thinking, plumb it
   through as a per-call kwarg — don't flip the default.
3. **Keep-alive + warm-up.** `keep_alive=-1` in the payload pins the model in
   VRAM for the duration of an active run. The systemd-level
   `OLLAMA_KEEP_ALIVE=10m` is the idle-unload fallback between scans, but the
   per-call value is what Ollama uses while a request is in flight, so the
   model never drops mid-pipeline. `scan_cmd._warm_model()` fires a tiny chat
   before the scoring loop so the first real call doesn't pay cold-load on
   top of the 180 s gateway timeout.
4. **Truncate inputs** to fit `num_ctx` (default 16384 — matches
   `OLLAMA_CONTEXT_LENGTH=16384`). The score/tailor pipelines truncate
   description to `MAX_DESC_CHARS=14000` and policy to `MAX_POLICY_CHARS=6000`
   — see `pipeline.score`. If you bump `num_ctx` again, bump these in
   step so the prompts use the additional room rather than leaving it idle.
5. **Default temperatures** are set in prompt frontmatter: scoring 0.0, tailoring 0.3, cover letters 0.7 (the cover prompt is tuned around the wider creative latitude — don't drop it back to 0.5 without re-tuning the anti-pattern rules).
6. **Honesty enforcement is structural.** The tailor pipeline's
   `_enforce_no_fabrication` rejects any role/employer/dates that diverge from
   `verified.json`, any skill not in `verified.json` (paren-substring tolerated),
   and any "Familiar" skill in a non-Familiar category. Adding a new tailoring
   capability MUST keep these checks green.
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

   **Auto-decline triggers (May 2026, intentionally narrow):** Senior /
   Lead / Staff / Principal / Architect titles are **not** declines on
   their own — IC roles at those titles are valid. Auto-decline only fires
   when the JD body explicitly names people-management responsibilities
   (mentoring 4+ direct reports, owning headcount, performance reviews),
   when the title is hard people-management (Manager/Director/Head of/VP),
   or when years required ≥ 7 with no transferable bridge. The 4+ hard-gap
   threshold requires at least one **Tier-1 ask** (phrasing like
   "required", "5+ years of", "strong production experience with") —
   vague "nice-to-haves" do not trigger.

   **`pipeline.min_score` defaults to 55** (lowered from 65 in May 2026).
   The 55-59 band is the "stretch, tailor required" zone where a strong
   AI/LLM cover hook can break through — Casey's highest-leverage band
   given his interview-rate situation. Raise back to 65 in config.toml if
   the list gets noisy.

## Post-generation audit rules

After `tailor_resume` + `write_cover`, `pipeline.audit.audit()` runs before
.docx render. It is **deterministic and LLM-free** — do not add an Ollama call
to it without explicit discussion.

1. **Keyword coverage** — JD must-haves (from the score result) must appear in
   the tailored resume at ≥70 % (2026 ATS guideline). Verdict `revise` if below.
   When `scores.reasons` is empty (qwen3.5:9b often ships empty arrays despite
   the schema requiring them), `audit._extract_must_haves_from_jd` runs as a
   deterministic fallback — intersect verified skills with `job_title ∪
   job_description`. Title is part of the source because Adzuna ships ~500-char
   description snippets where canonical tech names ("Java", "React") often
   only survive in the title. Adding new tailoring capabilities must not
   break this fallback path.
2. **Cover-letter validator** (`pipeline.cover_validate`) — enforces banned
   phrases (substring tier + structural `_DEFENSIVE_PATTERNS` regex tier for
   defensive gap-volunteering like "rather than X", "the model transfers"),
   word count, paragraph count, company name in lead (tokenized: splits on
   whitespace+punctuation, drops corporate suffixes like `Inc`/`Technologies`
   and TLD fragments like `.io`/`.ai` via `_COMPANY_STOPWORDS`, accepts any
   distinctive remaining token), no unverified numbers (digits embedded in
   alphanumeric tokens like ES6 are exempt), no closing diploma re-recap.
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
