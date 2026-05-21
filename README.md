# Job Hunt AI Buddy

A local-first CLI for Casey's Toronto-area job hunt. Pulls jobs from public
ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Job Bank
Canada, generic RSS, Adzuna CA), scoped to GTA + 100 km and Remote-Canada
postings. Fit-scores them against the parsed baseline resume using local
Ollama models, drafts a tailored resume and cover letter per role, answers
free-form application form questions, and assists with form autofill in the
browser. **You submit every application yourself.** The tool fills the
form; it never clicks Submit.

Everything runs locally. No resume or job data leaves your hardware. Zero
cloud LLM calls in the runtime path.

## TL;DR

```bash
# 30-second setup
git clone <repo>; cd jobhunt
uv sync && source .venv/bin/activate
ollama pull qwen3.5:9b
jobhunt db migrate && jobhunt convert-resume && jobhunt config seed --apply

# Daily
jobhunt scan
jobhunt list --verdict ship --no-reply
jobhunt apply --best
```

Full sections below; `<command> --help` for every flag.

## Non-goals

- **No LinkedIn / Indeed / Glassdoor scraping.** Public ATS APIs only.
- **No bot-submitted applications.** Human-in-the-loop on every submission.
- **No auto-account creation** on employer sites.
- **No stored employer credentials.** If a site needs login, you log in manually.

## Requirements

- Linux or macOS (developed on Arch Linux)
- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) for dependency management
- [Ollama](https://ollama.com) at `http://localhost:11434`
- ~10 GB VRAM for the default model
- Free Adzuna CA API key: <https://developer.adzuna.com/>

## Install

```bash
git clone <this-repo>
cd jobhunt
uv sync
source .venv/bin/activate        # puts `jobhunt` on PATH; or prefix commands with `uv run`
playwright install chromium

ollama pull qwen3.5:9b           # base model — all LLM tasks
ollama pull nomic-embed-text     # embeddings (reserved for future use)
```

Default model in config is `qwen-custom:latest` — a Modelfile-derived
`qwen3.5:9b` baking in personal prompt context. If you haven't built the
custom variant, set all `[gateway.tasks]` slots to `qwen3.5:9b` in
`~/.config/jobhunt/config.toml`. See [AGENTS.md](AGENTS.md) §Hardware
context for the full rationale.

### Ollama systemd settings

The gateway is tuned to a specific server config. Mirror these
(`sudo systemctl edit ollama.service`):

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q5_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_CONTEXT_LENGTH=16384"
Environment="OLLAMA_KEEP_ALIVE=10m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

Context length is the server's responsibility — the gateway does NOT send `num_ctx`. Pair only with the per-call `keep_alive=-1` the gateway uses.
Rationale and tuning notes live in [AGENTS.md](AGENTS.md) §Hardware context.

## First run

```bash
jobhunt config show            # writes a default config and prints it
jobhunt db init                # creates SQLite schema at data/jobhunt.db
jobhunt convert-resume         # generates kb/profile/* from Resume.docx
jobhunt config seed --apply    # primes config with verified GTA-employer slugs
```

> `config` and `db` are setup-only commands hidden from `--help` after install.

`scan`, `list`, `apply`, `answer`, and `analyze` will refuse to run until
`convert-resume` has been executed — they need `kb/profile/verified.json`
as the source of truth.

To start over (drops DB, tailored documents, HTTP cache, browser profile,
parsed resume):

```bash
jobhunt db reset               # prompts for 'yes', then re-inits schema
jobhunt convert-resume
```

## Commands

Run `<command> --help` for full flags. Grouped by purpose:

### Setup
`convert-resume` · `config seed` · `db migrate`

### Daily flow
`scan` · `list` · `apply` · `answer` · `interview-prep`

### Analysis (deterministic, no LLM)
`analyze certs` · `analyze skills --gaps` · `analyze employers --hiring-velocity` · `analyze response-rate` · `analyze validators`

### Maintenance
`add` · `config reprobe` · `discover slugs`

## Daily flow

```bash
jobhunt scan                          # pull new jobs + score
jobhunt scan --max-age-days 30        # widen the 7-day freshness window (0 disables)
jobhunt list --verdict ship           # only audited ship-verdict drafts
jobhunt list --min-score 75           # high-fit subset (default floor is 55)
jobhunt apply --best                  # interactive picker over top 10
# Browser opens. You review, click Submit yourself.

# One-off posting from a URL (bypass scan):
jobhunt apply --url https://jobs.example.com/p/12345

# Form has a free-form question? Draft a tailored answer:
jobhunt answer "Why are you interested in this role?" --job <id>
jobhunt answer "leaving role" --recall    # recall past drafts by phrase

# When you get an interview, draft the prep doc (recruiter type biases questions):
jobhunt interview-prep <job-id> --stage screen --research

jobhunt list --week 0                 # weekly pipeline view
```

Scan filters: 7-day freshness (Workday is included as of the
`postedOn`-parsing adapter), no management titles (Manager / Director /
Head / VP — Senior / Lead / Staff are kept as IC roles).

After a batch `apply --top N` or `apply --best` run, the tool prints a
one-line summary: how many drafted / revised / blocked plus the top
warning categories. Between jobs, you get a `continue? [Y]/n` prompt — the
next job's tailor+cover is already running in the background, so saying
"yes" gets you a draft that's already done; "no" cancels it cleanly.

### Tracking responses & outcomes

The application lifecycle is recorded on disk so `analyze response-rate`
can show interview rate per score band. Lifecycle flags use `apply` as
the entry point but bypass re-tailoring:

```bash
# Recruiter replies — tag who they are (drives interview-prep question bias)
jobhunt apply <id> --mark-response 2026-05-21 \
                   --recruiter-type hiring_manager

# Interview scheduled — auto-promotes status to 'interviewing'
jobhunt apply <id> --mark-interview 2026-05-25

# Terminal outcome — distinct from status
jobhunt apply <id> --set-outcome offer        # or rejected / withdrawn / ghosted

# Find applications waiting for a response (the nudge list)
jobhunt list --no-reply --older-than 14d
```

Recruiter types: `internal_recruiter`, `hiring_manager`, `external_agency`,
`unknown`. `interview-prep` reads this when drafting questions (or you
can override with `--recruiter-type <type>` for cold prep before any
recruiter contact).

## Configuration

`~/.config/jobhunt/config.toml`:

```toml
[ingest]
greenhouse      = []   # board slugs, e.g. "faire"
lever           = []   # board slugs, e.g. "benchsci"
ashby           = []   # board slugs, e.g. "cohere"
smartrecruiters = []   # company slugs, case-sensitive (e.g. "Bosch", "Visa")
workday         = []   # "tenant:host:site" triples (see ingest/workday.py)
job_bank_ca     = []   # full RSS URLs from jobbank.gc.ca search results
rss             = []   # generic employer career-page RSS/Atom URLs
max_age_days    = 7    # drop postings older than N days at ingest (0 disables)

[ingest.adzuna]
# Empty list = auto-derive from kb/profile/verified.json (skills + bullets).
# Populate to override with a verbatim list.
queries = []

[applicant]
phone = "(416) 555-0123"
salary_expectation_cad = "50k–90k"

[pipeline]
min_score = 55              # apply / list default floor
answer_max_words = 200      # `answer` default word cap
```

API keys live in `~/.config/jobhunt/secrets.toml` (chmod 0600) or env vars:

```toml
adzuna_app_id  = "..."
adzuna_app_key = "..."
```

The full Pydantic schema with every default lives in
[src/jobhunt/config.py](src/jobhunt/config.py).

## Slug acquisition — `add`, `config seed`, `config reprobe`, `discover slugs`

Adzuna ships short JD snippets (~500 chars). Greenhouse, Lever, Ashby,
SmartRecruiters, and Workday return full descriptions, but each employer
needs a slug in `config.toml`. Four workflows:

- **`jobhunt add <URL>`** — daily driver. Paste any recognized career-page
  URL; the tool parses the ATS, probes once, appends to config.
  Recognized hosts: `boards.greenhouse.io`, `jobs.lever.co`,
  `jobs.ashbyhq.com`, `jobs.smartrecruiters.com`, `*.wd*.myworkdayjobs.com`.

- **`jobhunt config seed --apply`** — cold start. Imports the curated
  `kb/seeds/gta-employers.toml`. Every entry is probe-verified via
  `scripts/verify_seeds.py` before being committed, so dead slugs don't
  ship. `scripts/seed_mars.py` is a companion that probes the Toronto AI
  / MaRS-grade startup space and emits paste-ready TOML.

- **`jobhunt config reprobe`** — quarterly hygiene. Re-probes every
  configured greenhouse / lever / ashby / smartrecruiters slug; prints
  live vs stale. `--prune` removes stale entries (`--force` skips the
  confirmation prompt). Workday is skipped — CXS handshake isn't a cheap
  probe.

- **`jobhunt discover slugs`** — bulk maintenance. Harvests confirmed
  slugs from URLs already in your jobs DB, then probes public APIs for
  unconfigured companies. `--apply` appends hits; misses cache in
  `slug_probes` so repeat runs only probe new companies.

> **Heads-up:** all writers (`add`, `config seed --apply`,
> `config reprobe --prune`, `discover slugs --apply`) write `config.toml`
> programmatically — inline comments are stripped on write, but a `.bak`
> snapshot is created.

After `apply --url <some-careers-page>`, the tool prints a `jobhunt add`
suggestion if the URL belongs to an ATS you haven't configured yet —
slug acquisition as a byproduct of normal use.

## Command details

### `apply` selection modes

- **`apply <job-id>`** — single job by id.
- **`apply --top N`** — N highest-scoring unapplied jobs above `--min-score`
  (default 55). Capped at 20. Shows a `continue? [Y]/n` prompt between
  jobs so you can exit cleanly mid-batch.
- **`apply --best`** — top 10 candidates with an interactive picker
  (`1,3,7` or `2-5`). Pair with `--include-borderline` to also surface up
  to 10 stretch jobs in the `[min_score-10, min_score)` band, labelled
  `stretch`.
- **`apply --url <URL>`** — bypass `scan` for a one-off posting. Fetches
  in headless Chromium so JS-heavy portals load JD content. Use
  `--title` / `--company` if auto-detection misses. `--no-score` skips
  scoring; `--force-robots` overrides robots.txt for that single fetch.

Add `--no-browser` to any `apply` invocation to generate tailored docs
without launching Playwright.

Lifecycle flags (covered in [Tracking responses & outcomes](#tracking-responses--outcomes)):
`--set-status`, `--mark-response`, `--recruiter-type`, `--mark-interview`,
`--set-outcome`.

### `answer` — application-form question assistant

```bash
# Standalone — no JD context
jobhunt answer "Why are you looking for a new role right now?"

# Job-scoped — loads the JD for richer framing
jobhunt answer "Why us?" --job adzuna_ca:5730918359

# Tune length per question type
jobhunt answer "Years of TypeScript?" --max-words 60
jobhunt answer "Walk me through a project" --max-words 250

# Skip the .md artifact, print to stdout only
jobhunt answer "Anything else?" --no-save

# Search past drafts by phrase
jobhunt answer "leaving role" --recall
```

Validation reuses the cover-letter rules (banned phrases, defensive
gap-volunteering, fabrication watchlist, unverified numbers). Up to 3
retries on violations. Artifacts save to
`data/applications/<job-id>/answers/<sha1>.md` (job-scoped) or
`data/answers/<sha1>.md` (standalone). Filename is a sha1 of the
question text — re-running the same question overwrites and re-indexes.

### `interview-prep` — stage-aware interview prep doc

```bash
jobhunt interview-prep <job-id>                       # default --stage screen
jobhunt interview-prep <job-id> --stage hm            # hiring-manager round
jobhunt interview-prep <job-id> --stage assessment    # take-home / live coding
jobhunt interview-prep <job-id> --stage onsite        # final round
jobhunt interview-prep <job-id> --research            # fetch JD URL + company root
jobhunt interview-prep <job-id> --research --refresh-research  # bypass per-day cache
jobhunt interview-prep <job-id> --recruiter-type hiring_manager  # override recorded type
jobhunt interview-prep <job-id> --no-llm              # skeleton-only (debug)
```

Output: `data/interview-prep/<job-id-safe>.md`. Re-runs overwrite — use
the same file as the doc evolves across stages.

Hybrid generation: deterministic skeleton (header, comp heads-up,
pre-call checklist, after-the-call footer) wraps an LLM-drafted middle
(role decode, strongest anchors, likely questions with answer beats,
questions to ask back, honest gaps with reframes). Anchors must trace to
verified facts; the same honesty rules as the cover-letter pipeline
apply. Up to 3 retries on validator violations.

Question mix bias by recruiter type: internal recruiters → behavioral +
comp; hiring managers → deep technical + team-fit; external agencies →
personal/soft-skills; unknown → balanced.

Research fetches cache at `data/research-cache/<host>/<yyyy-mm-dd>__<hash>.txt`.
Same-day hits reuse; `--refresh-research` forces refetch. Numeric noise
(decimals, currency, thousands-separated counts) is scrubbed from
fetched HTML before it enters the prompt to prevent the LLM from
parroting employer stats as Casey's facts.

### `list` filters

```
--week N                 0=current, 1=last, …
--status                 drafted | applied | interviewing | offer | rejected
--min-score N
--source                 greenhouse | lever | ashby | smartrecruiters | workday |
                         job_bank_ca | rss | adzuna_ca
--verdict                ship | revise | block       (reads audit.json)
--no-reply                                           (applied with no response logged)
--older-than 14d|2w                                  (applied_at older than now-N)
```

Default sort: ship-verdict first then score desc. Always renders a
weekly rollup footer (scanned / declined / per-status counts). Rows for
audited jobs show a `cov=NN%` tag from the latest `audit.json` plus the
verdict.

### Analyze — deterministic feedback loop

All five subcommands are LLM-free regex/SQL aggregations over the
jobs/scores/applications tables. See [AGENTS.md](AGENTS.md) for the full
behavior spec.

**`analyze certs`** — frequency, trends, fit verdicts.

```bash
jobhunt analyze certs                          # snapshot
jobhunt analyze certs --trend                  # prev vs current 30d Δ%
jobhunt analyze certs --trend --min-score 55   # fit-filtered + Verdict column
```

The Verdict column (only with `--min-score`) classifies each cert against
a rubric (Worth pursuing / Skip / Wrong direction / etc.) so the
decision is one column wide.

**`analyze skills --gaps`** — tech tokens over-represented in declined
JDs vs accepted JDs over the window. Surfaces what the market keeps
asking for that you keep declining on.

**`analyze employers --hiring-velocity`** — post counts per configured
slug within the window. Surfaces configured-but-zero-posts slugs as
candidates for `config reprobe`.

**`analyze response-rate [--by score|ats]`** — interview/response rate
per bucket. Uses the `--mark-response` / status data — apply with the
lifecycle flags before this gives you useful numbers.

**`analyze validators`** — which cover-letter validators fired most in
recent audits. Use it to find over-broad rules to prune; if
`banned_phrase` is hot, the watchlist is too aggressive.

## Data layout

| Path | What lives there |
|---|---|
| `Resume.docx` | Source-of-truth resume. Hand-edited. |
| `Resume_Tailoring_Instructions.md` | Hard rules (no fabrication, ATS-safe, auto-decline). |
| `kb/profile/verified.json` | Structured facts emitted by `convert-resume`. |
| `kb/policies/tailoring-rules.md` | Prompt-injectable mirror of the tailoring rules. |
| `kb/prompts/{score,tailor,cover,answer,interview-prep}.md` | Prompts with JSON-schema frontmatter. |
| `kb/seeds/gta-employers.toml` | Curated verified ATS slugs (imported by `config seed`). |
| `~/.config/jobhunt/config.toml` | Sources, models, applicant profile, paths. |
| `~/.config/jobhunt/secrets.toml` | API keys (Adzuna), mode 0600. |
| `data/jobhunt.db` | SQLite — jobs, scores, applications, answers, slug_probes. |
| `data/applications/<job-id>/` | Tailored resume, cover letter, `audit.json`, `tailor-diff.md`, `fill-plan.json`, `answers/`. |
| `data/answers/<sha1>.md` | Standalone (non-job-scoped) answer artifacts. |
| `data/interview-prep/<job-id>.md` | Stage-aware interview prep doc. |
| `data/research-cache/<host>/<date>__<hash>.txt` | Per-day cached interview-prep research. |
| `data/cache/` | Cached raw HTTP responses (TTL-based). |

`data/` is gitignored.

## For maintainers

This repo carries four agent-facing docs. Edit them in this order; they
cite each other and stay in sync via the cross-tool `AGENTS.md` convention.

- [AGENTS.md](AGENTS.md) — guardrails, conventions, project structure,
  pipeline rules. The *how*. Source of truth for any agent (Claude Code,
  Cursor, Codex, Aider) working in this repo.
- [PLAN.md](PLAN.md) — design rationale. The *why*. Goals, model choice,
  honesty-enforcement layers, sources, success criteria.
- [CLAUDE.md](CLAUDE.md) — tiny stub that `@`-imports AGENTS.md so
  Claude Code's auto-load works. Don't edit it; edit AGENTS.md.
- [Resume_Tailoring_Instructions.md](Resume_Tailoring_Instructions.md) —
  honesty rules enforced by the tailor pipeline. Bucket placements,
  things Casey hasn't done, when to tell Casey "no".

Honesty enforcement is structural (verified-snapshot constraint,
schema-bounded output, post-decode invariants, score clamp, cover and
tailor validators + retry, resume↔cover alignment check). See
[AGENTS.md](AGENTS.md) §LLM call rules and §Post-generation audit rules
for the full mechanism.

## License

Copyright (c) [2026] [Casey Hsu]

Permission is hereby denied :D
