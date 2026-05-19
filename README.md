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

## Daily flow

```bash
jobhunt scan                   # pull new jobs + score them (filters: 14-day freshness, no management titles)
jobhunt scan --max-age-days 30 # widen the freshness window for a specific run (0 disables)
jobhunt list --min-score 70    # high-fit subset
jobhunt apply --best           # pick which to apply to
# Browser opens. You review, click Submit yourself.

# One-off posting from a URL (bypass scan):
jobhunt apply --url https://jobs.example.com/p/12345

# When a form has a free-form question:
jobhunt answer "Why are you interested in this role?" --job <id>

# When you get an interview, mark it and draft a prep doc:
jobhunt apply --set-status interviewing <job-id>
jobhunt interview-prep <job-id> --stage screen --research

jobhunt list --week 0          # weekly pipeline view
```

After a batch `apply --top N` or `apply --best` run, the tool prints a
one-line summary: how many drafted / revised / blocked, plus the top
warning categories seen across the batch.

## Commands

Nine user-facing commands. Run `<command> --help` for full flags.

| Command | Purpose |
|---|---|
| `convert-resume` | Parse `Resume.docx` → `kb/profile/` |
| `scan` | Ingest GTA jobs + score against profile |
| `apply` | Tailor resume + cover letter; autofill the form |
| `add` | URL → ATS slug → `config.toml` |
| `answer` | Draft a tailored response to a form question |
| `interview-prep` | Draft a stage-aware interview prep doc |
| `list` | Pipeline view + weekly tracking |
| `analyze certs` | Cert frequency, trends, fit verdicts |
| `discover slugs` | Maintenance: harvest URLs in jobs DB + probe public ATS APIs |

`config seed --apply` is part of the user-facing onboarding flow; the rest
of `config` and all of `db` are hidden internals.

### `apply` selection modes

- **`apply <job-id>`** — single job by id.
- **`apply --top N`** — N highest-scoring unapplied jobs above `--min-score`
  (default 55, set in `[pipeline] min_score`). Capped at 10.
- **`apply --best`** — top 10 candidates with an interactive picker
  (`1,3,7` or `2-5`). Pair with `--include-borderline` to also surface up
  to 10 stretch jobs in the `[min_score-10, min_score)` band, labelled
  `stretch`, for days when the high-fit list is dry.
- **`apply --url <URL>`** — bypass `scan` for a one-off posting. Fetches
  the page in headless Chromium so JS-heavy portals (Workday, Phenom,
  iCIMS) load their JD content. Use `--title` / `--company` if
  auto-detection misses. Escape hatches: `--no-score` skips scoring;
  `--force-robots` overrides the robots.txt check for that single fetch.

Add `--no-browser` to any `apply` invocation to generate tailored docs
without launching Playwright.

After submitting, update status:

```bash
jobhunt apply --set-status applied      <job-id>
jobhunt apply --set-status interviewing <job-id>
jobhunt apply --set-status rejected     <job-id>
```

The flag comes **before** the job id.

### `answer` — application-form question assistant

When a form asks free-form questions ("Why are you interested in this
role?", "Describe a project where you faced a technical challenge"), paste
the question into `jobhunt answer` and get a tailored response drafted
against your verified profile under the same honesty rules as cover
letters.

```bash
# Standalone — no JD context, useful for recruiter screens / general intros
jobhunt answer "Why are you looking for a new role right now?"

# Job-scoped — loads the JD from the jobs table for richer framing
jobhunt answer "Why us?" --job adzuna_ca:5730918359

# Tune the length budget per question type
jobhunt answer "Years of TypeScript?" --max-words 60        # short-factual
jobhunt answer "Walk me through a project" --max-words 250  # STAR-style

# Skip the .md artifact, print to stdout only
jobhunt answer "Anything else?" --no-save
```

The answer prints between separator bars (clean copy-paste target). By
default it's also saved to:

- `data/applications/<job-id>/answers/<sha1>.md` when `--job` is set
- `data/answers/<sha1>.md` otherwise

Filename is a sha1 of the question text — re-running the same question
overwrites the same file. Validation reuses the cover-letter rules
(banned phrases, defensive gap-volunteering, fabrication watchlist,
unverified numbers). Up to 3 retries on violations; the final attempt
ships with warnings on stderr if retries don't recover.

### `interview-prep` — stage-aware interview prep doc

When an application converts to an interview, draft a prep doc anchored
on your verified profile and the cached JD:

```bash
jobhunt interview-prep <job-id>                       # default --stage screen
jobhunt interview-prep <job-id> --stage hm            # hiring-manager round
jobhunt interview-prep <job-id> --stage assessment    # take-home / live coding
jobhunt interview-prep <job-id> --stage onsite        # final round
jobhunt interview-prep <job-id> --research            # fetch JD URL + company root
jobhunt interview-prep <job-id> --research --force-robots
jobhunt interview-prep <job-id> --no-llm              # skeleton-only (debug)
```

Output: `data/interview-prep/<job-id-safe>.md`. Re-runs overwrite — use
the same file as the doc evolves across stages.

Hybrid generation: deterministic skeleton (header, comp heads-up,
pre-call checklist, after-the-call footer) wraps an LLM-drafted middle
(role decode, strongest anchors, likely questions with answer beats,
questions to ask back, honest gaps with reframes). Anchors must trace
to verified facts; the same honesty rules as the cover-letter pipeline
apply. Up to 3 retries on validator violations.

`apply --set-status interviewing <job-id>` prints a nudge pointing at
this command (or notes that a prep doc already exists).

### `list` filters

```
--week N                 0=current, 1=last, …
--status                 drafted | applied | interviewing | offer | rejected
--min-score N
--source                 greenhouse | lever | ashby | smartrecruiters | workday |
                         job_bank_ca | rss | adzuna_ca
```

Always renders a weekly rollup footer (scanned / declined / per-status
counts). Rows for jobs you've already run `apply` on show a `cov=NN%` tag
— the keyword-coverage % from the most recent `audit.json`. Drafted
applications with `cov < 70%` are good candidates for a re-tailor.

### Slug acquisition — `add`, `config seed`, `discover slugs`

Adzuna ships short JD snippets (~500 chars). Greenhouse, Lever, Ashby,
SmartRecruiters, and Workday return full descriptions, but each employer
needs a slug in `config.toml`. Three workflows fill that list:

1. **`jobhunt add <URL>`** — the daily driver. Paste any recognized
   career-page or job-posting URL; the tool parses the ATS, probes once
   to confirm, and appends to config.

   ```bash
   jobhunt add https://boards.greenhouse.io/faire
   jobhunt add https://jobs.ashbyhq.com/cohere
   jobhunt add https://rbc.wd3.myworkdayjobs.com/en-US/RBC_Careers
   ```

   Recognized hosts: `boards.greenhouse.io`, `jobs.lever.co`,
   `jobs.ashbyhq.com`, `jobs.smartrecruiters.com`,
   `*.wd*.myworkdayjobs.com`. iCIMS URLs are recognized but exit with
   "coming soon" — no adapter yet.

2. **`jobhunt config seed`** — cold start. Imports the live-verified
   seed list from `kb/seeds/gta-employers.toml`. Every entry is
   probe-checked via `scripts/verify_seeds.py` before being committed,
   so dead slugs don't ship.

   ```bash
   jobhunt config seed --preview   # see what would be added
   jobhunt config seed --apply     # additively merge into config.toml
   ```

3. **`jobhunt discover slugs`** — maintenance. Harvests confirmed slugs
   from URLs already in your jobs DB (offline, no HTTP), then probes
   public Greenhouse / Lever / Ashby / SmartRecruiters APIs for company
   names not yet covered.

   ```bash
   jobhunt discover slugs                     # print suggestions (default --limit 100)
   jobhunt discover slugs --apply             # append confirmed slugs to config.toml
   jobhunt discover slugs --ats greenhouse    # restrict probe targets
   jobhunt discover slugs --include-cached    # re-probe past misses
   ```

   Misses are cached in the `slug_probes` table so repeat runs only probe
   new companies. Zero-job 200 responses are treated as misses.
   Staffing-agency names (Astra North, Targeted Talent, etc.) are
   filtered at the candidate stage and never hit the network.

After `apply --url <some-careers-page>`, the tool prints a `jobhunt add`
suggestion if the URL belongs to an ATS you haven't configured yet —
slug acquisition as a byproduct of normal use.

> **Heads-up:** all three writers (`add`, `config seed --apply`,
> `discover slugs --apply`) write `config.toml` programmatically — any
> inline comments you've added will be stripped on write. A `.bak`
> snapshot is created next to the file.

### `analyze certs` — cert decision tool

Three modes, all deterministic and LLM-free:

```bash
jobhunt analyze certs                          # snapshot frequency (default 25 rows)
jobhunt analyze certs --trend                  # prev vs current 30d windows + Δ% + trend label
jobhunt analyze certs --trend --min-score 55   # fit-filtered: adds Fit column + Verdict
```

Windows bucket by `COALESCE(posted_at, ingested_at)`. Use `--window-days N`
to widen or narrow (default 30).

The Verdict column (only with `--min-score`) classifies each cert against
a small rubric so the decision is one column wide:

| Verdict | Means |
|---|---|
| Strong emerging signal | New in current window, fit_cur ≥ 3 |
| Worth pursuing | Rising 50 %+, fit_cur ≥ 3 |
| Stable staple | Steady demand, fit_cur ≥ 3, top-10 by market presence |
| Marginal | Stable but not top of market |
| Late — diminishing | Falling 50 %+, even if fit_cur ≥ 3 |
| Skip | Fewer than 3 jobs you'd qualify for mention it |
| Wrong direction | Common in the market (cur ≥ 5) but zero fit-eligible jobs |

`--min-score N` joins the `scores` table. `N` should match your apply
threshold (default 55 — set in `[pipeline] min_score`).

The `Potential new certs` section surfaces generic-regex hits
(`Certified <Noun>` / `<Noun> Certification`) that aren't in the curated
`_KNOWN` list in `src/jobhunt/analyze/certs.py`. Review and promote real
catches by hand.

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

See [Slug acquisition](#slug-acquisition--add-config-seed-discover-slugs)
for filling in the ATS slug lists. The full Pydantic schema with every
default lives in [src/jobhunt/config.py](src/jobhunt/config.py).

API keys live in `~/.config/jobhunt/secrets.toml` (chmod 0600) or env vars:

```toml
adzuna_app_id  = "..."
adzuna_app_key = "..."
```

## Data layout

| Path | What lives there |
|---|---|
| `Resume.docx` | Source-of-truth resume. Hand-edited. |
| `Resume_Tailoring_Instructions.md` | Hard rules (no fabrication, ATS-safe, auto-decline). |
| `kb/profile/verified.json` | Structured facts emitted by `convert-resume`. |
| `kb/policies/tailoring-rules.md` | Prompt-injectable mirror of the tailoring rules. |
| `kb/prompts/{score,tailor,cover,answer}.md` | Prompts with JSON-schema frontmatter. |
| `kb/seeds/gta-employers.toml` | Curated verified ATS slugs (imported by `config seed`). |
| `~/.config/jobhunt/config.toml` | Sources, models, applicant profile, paths. |
| `~/.config/jobhunt/secrets.toml` | API keys (Adzuna), mode 0600. |
| `data/jobhunt.db` | SQLite — jobs, scores, applications, slug_probes. |
| `data/applications/<job-id>/` | Tailored resume, cover letter, `audit.json`, `fill-plan.json`, `answers/`. |
| `data/answers/<sha1>.md` | Standalone (non-job-scoped) answer artifacts. |
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
