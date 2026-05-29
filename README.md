# Job Hunt AI Buddy

A local-first CLI for Casey's Toronto-area job hunt. Pulls jobs from public
ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Workable,
Recruitee, Job Bank Canada, generic RSS, Adzuna CA), scoped to GTA +
100 km and Remote-Canada postings. After each scan, the tool probes public
ATS APIs for slugs of newly-seen companies and auto-appends hits to
`config.toml`, so the next scan pulls deep JDs natively — slug curation is
mostly automatic. Fit-scores them against the parsed baseline resume using local
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
git clone https://github.com/SimBuds/Jobhunt
cd jobhunt

uv sync
source .venv/bin/activate        # puts `jobhunt` on PATH; or prefix commands with `uv run`
playwright install chromium

ollama pull qwen3.5:9b           # base model — all LLM tasks
ollama pull nomic-embed-text     # embeddings (reserved for future use)
```

Default model in config is base `qwen3.5:9b`. The gateway supplies its own task
prompt and its own options — notably `num_ctx=16384` and `presence_penalty=0` —
so behavior is defined in-repo and no custom Modelfile is needed. The
`num_ctx=16384` is essential: these prompts exceed Ollama's 4096 default, and
without it they truncate and the model returns prose instead of JSON. See
[AGENTS.md](AGENTS.md) Hardware context for the full rationale.

### Ollama systemd settings

The gateway is tuned to a specific server config. Mirror these
(`sudo systemctl edit ollama.service`):

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q5_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

Pair only with the per-call `keep_alive=-1` the gateway uses.
Rationale and tuning notes live in [AGENTS.md](AGENTS.md) Hardware context.

## First run

Drop your baseline resume at `./Resume.docx`, then:

```bash
jobhunt setup
```

The wizard walks you through every first-run step in order:

1. Initialize the SQLite DB and run migrations.
2. Confirm `Resume.docx` is in place.
3. Parse it into `kb/profile/verified.json` + the markdown sidecars.
4. Prompt for applicant defaults — `years_experience`,
   `include_senior_roles`, salary, work arrangements, employment types.
5. Print the resolved config via `config show`.
6. Preview the curated GTA-employer seed list and offer to apply it.

Re-run `jobhunt setup` any time to update applicant defaults — each step
detects existing state and offers keep/redo. No destructive defaults.

If you'd rather do it by hand, the equivalent manual sequence is:

```bash
jobhunt config show            # writes a default config and prints it
jobhunt db init                # creates SQLite schema at data/jobhunt.db
jobhunt convert-resume         # generates kb/profile/* from Resume.docx
# hand-edit ~/.config/jobhunt/config.toml to fill [applicant] fields
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
jobhunt setup
```

## Daily flow

```bash
jobhunt scan                   # pull new jobs + score them (filters: 7-day freshness, no management titles)
jobhunt scan --max-age-days 30 # widen the freshness window for a specific run (0 disables)
jobhunt scan --no-discover     # skip the post-ingest slug auto-discovery step
jobhunt list --min-score 70    # high-fit subset
jobhunt apply --best           # pick which to apply to
# Browser opens. You review, click Submit yourself.

# One-off posting from a URL (bypass scan):
jobhunt apply --url https://jobs.example.com/p/12345

# When a form has a free-form question:
jobhunt answer "Why are you interested in this role?" --job <id>

# When you get an interview, mark it and draft a prep doc:
jobhunt apply --set-status interviewing <job-id>
jobhunt interview-prep <job-id> --stage agency --research

jobhunt list --week 0          # weekly pipeline view
```

After a batch `apply --top N` or `apply --best` run, the tool prints a
one-line summary: how many drafted / revised / blocked, plus the top
warning categories seen across the batch.

## Maintenance

The pipeline is self-bootstrapping (auto-discovery on every scan), but a
small amount of recurring hygiene keeps the slug list, cert intel, and
follow-up queue honest.

### Daily

```bash
jobhunt scan                       # pulls new jobs, scores them, auto-discovers new slugs
jobhunt list --min-score 70        # high-fit subset
jobhunt apply --best                # tailor + draft for the day's picks
# …submit in browser, then:
jobhunt apply --set-status applied <job-id>
```

If the scan summary shows `! <slug>: 404 …`, note the slug but don't
panic — transient 404s happen. The weekly `config reprobe --prune` pass
is the right place to act on it.

### Weekly

```bash
jobhunt list --week 0                          # current-week pipeline rollup
jobhunt list --no-reply --older-than 14d      # applications without a recruiter reply — nudge candidates
jobhunt config reprobe --prune                 # re-probe every configured slug; prune dead ones (404s, dropped boards)
jobhunt analyze response-rate --by score       # interview rate per score band — feedback on `pipeline.min_score`
jobhunt analyze certs --trend --min-score 55   # cert intel + per-cert verdict
jobhunt analyze employers --hiring-velocity    # surfaces configured-but-silent slugs as reprobe candidates
jobhunt analyze validators                     # which cover-letter validators fired most — prune over-broad rules
```

`config reprobe --prune` is the safety valve for the 404s the scan
summary keeps surfacing — it confirms each dead slug with a fresh probe
before removing it, so transient outages don't drop real boards. Run
`config reprobe` (no `--prune`) first if you want a preview.

After ~20 applications, run `jobhunt config calibrate` to see the
interview rate per score band and tune `[pipeline] min_score`.

## Commands

Ten user-facing commands, grouped by workflow stage below. Run
`<command> --help` for the full flag list. `config seed --apply` is part of
onboarding; the rest of `config` and all of `db` are hidden setup-only internals.

| Command | Stage | Purpose |
|---|---|---|
| `setup` | Setup | Guided first-run wizard (DB init, resume parse, applicant defaults, seed import) |
| `convert-resume` | Setup | Parse `Resume.docx` → `kb/profile/` |
| `scan` | Find | Ingest GTA jobs + score against profile |
| `list` | Find | Pipeline view + weekly rollup |
| `apply` | Apply | Tailor resume + cover letter; autofill the form (you submit) |
| `answer` | Apply | Draft a tailored response to a form question |
| `interview-prep` | Apply | Stage-aware interview prep doc |
| `add` | Slugs | URL → ATS slug → `config.toml` |
| `discover slugs` | Slugs | Harvest URLs in the jobs DB + probe public ATS APIs |
| `analyze` | Analyze | Deterministic intel (certs, skills, employers, validators, response-rate) |

### Setup & profile

#### `setup`

Guided first-run wizard — see [First run](#first-run) for the full step list.
Safe to re-run any time to update applicant defaults; each step detects
existing state and offers keep/redo.

#### `convert-resume`

Parses `./Resume.docx` into `kb/profile/verified.json` plus markdown sidecars
(`skills.md`, `work-history.md`, `education.md`). `Resume.docx` is the single
source of truth — re-run after editing it. `scan`, `list`, `apply`, `answer`,
and `analyze` refuse to run until this has produced `verified.json`.

```bash
jobhunt convert-resume
```

### Finding & scoring jobs

#### `scan`

Ingests GTA + Remote-Canada postings from every configured source, scores each
against your profile, dedupes across sources, and (by default) auto-discovers
new ATS slugs after ingest.

```bash
jobhunt scan                    # pull new jobs + score + auto-discover slugs
jobhunt scan --max-age-days 30  # widen the freshness window (default 7; 0 disables)
jobhunt scan --no-discover      # skip the post-ingest slug auto-discovery step
```

Pre-score filters: 7-day freshness, management-title drop, plus optional
senior-title and research/ML-title drops (`[applicant] include_senior_roles`,
`[ingest] drop_research_titles`). A warm-up call fires before the scoring loop
so the first real call doesn't pay cold-load.

#### `list`

Pipeline view + weekly rollup. Always renders a footer (scanned / declined /
per-status counts). Rows you've run `apply` on show a `cov=NN%` tag — the
keyword-coverage % from the latest `audit.json`; `cov < 70%` is a re-tailor
candidate.

```bash
jobhunt list --week 0                       # 0=current week, 1=last, …
jobhunt list --min-score 70                 # high-fit subset
jobhunt list --status interviewing          # drafted|applied|interviewing|offer|rejected
jobhunt list --verdict ship                 # audit verdict: ship|revise|block
jobhunt list --source greenhouse            # greenhouse|lever|ashby|smartrecruiters|workday|
                                            #   workable|recruitee|job_bank_ca|rss|adzuna_ca
jobhunt list --no-reply --older-than 14d    # applied, no recruiter reply, >14d — nudge list
```

### Applying

#### `apply`

Tailors a resume + cover letter for a job, runs the deterministic audit, then
opens a headed browser and fills the form — **you review and click Submit
yourself.** Add `--no-browser` to any invocation to generate docs only.

Selection modes:

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

After submitting, update status (the flag comes **before** the job id):

```bash
jobhunt apply --set-status applied      <job-id>
jobhunt apply --set-status interviewing <job-id>
jobhunt apply --set-status rejected     <job-id>
```

After a batch `apply --top N` / `apply --best` run, the tool prints a one-line
summary: how many drafted / revised / blocked, plus the top warning categories
across the batch.

#### `answer`

When a form asks free-form questions ("Why are you interested in this role?",
"Describe a project where you faced a technical challenge"), paste the question
into `jobhunt answer` and get a response drafted against your verified profile
under the same honesty rules as cover letters.

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

The answer prints between separator bars (clean copy-paste target). By default
it's also saved to `data/applications/<job-id>/answers/<sha1>.md` (with `--job`)
or `data/answers/<sha1>.md` (standalone). Filename is a sha1 of the question
text, so re-running the same question overwrites the same file. Validation
reuses the cover-letter rules (banned phrases, defensive gap-volunteering,
fabrication watchlist, unverified numbers) with up to 3 retries.

#### `interview-prep`

When an application converts to an interview, draft a prep doc anchored on your
verified profile and the cached JD.

```bash
jobhunt interview-prep <job-id>                        # default --stage agency
jobhunt interview-prep <job-id> --stage hiring_manager # hiring-manager round
jobhunt interview-prep <job-id> --stage assessment     # assessment / final round
jobhunt interview-prep <job-id> --research             # fetch JD URL + company root
jobhunt interview-prep <job-id> --research --force-robots
jobhunt interview-prep <job-id> --no-llm               # skeleton-only (debug)
```

Output: `data/interview-prep/<job-id-safe>.md` (re-runs overwrite). Hybrid
generation — a deterministic skeleton (header, comp heads-up, pre-call
checklist, footer) wraps an LLM-drafted middle (role decode, anchors, likely
questions with answer beats, questions to ask back, honest gaps with reframes).
Anchors must trace to verified facts; same honesty rules + 3 retries as covers.
`apply --set-status interviewing` prints a nudge pointing here.

### Slug acquisition — automatic, with manual fallbacks

Adzuna ships short JD snippets (~500 chars); Greenhouse, Lever, Ashby,
SmartRecruiters, Workday, Workable, and Recruitee return full descriptions but
need a slug in `config.toml`. **`jobhunt scan` self-bootstraps this** — after
ingest it probes the public APIs for slugs of newly-seen aggregator-only
companies and appends hits to `config.toml` (with a `.bak`). Toggle off with
`jobhunt scan --no-discover` or `[ingest] auto_discover = false`.

The three manual paths below remain for cold-start and one-offs.

#### `add`

The daily driver. Paste any recognized career-page or job-posting URL; the tool
parses the ATS, probes once to confirm, and appends to config.

```bash
jobhunt add https://boards.greenhouse.io/faire
jobhunt add https://jobs.ashbyhq.com/cohere
jobhunt add https://rbc.wd3.myworkdayjobs.com/en-US/RBC_Careers
```

Recognized hosts: `boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`,
`jobs.smartrecruiters.com`, `*.wd*.myworkdayjobs.com`, `apply.workable.com`,
`*.workable.com`, `*.recruitee.com`. iCIMS URLs are recognized but exit with
"coming soon" — no adapter yet. After `apply --url <careers-page>`, the tool
prints an `add` suggestion if the URL belongs to an unconfigured ATS.

#### `config seed`

Cold start. Imports the live-verified seed list from
`kb/seeds/gta-employers.toml`; every entry is probe-checked via
`scripts/verify_seeds.py` before being committed, so dead slugs don't ship.

```bash
jobhunt config seed --preview   # see what would be added
jobhunt config seed --apply     # additively merge into config.toml
```

#### `discover slugs`

Maintenance. The same machinery `scan` uses, runnable on demand against the full
jobs DB. Harvests confirmed slugs from URLs already in the DB (offline), then
probes the public Greenhouse / Lever / Ashby / SmartRecruiters / Workable /
Recruitee APIs for company names not yet covered.

```bash
jobhunt discover slugs                     # print suggestions (default --limit 100)
jobhunt discover slugs --apply             # append confirmed slugs to config.toml
jobhunt discover slugs --ats greenhouse    # restrict probe targets
jobhunt discover slugs --include-cached    # re-probe past misses inside the TTL
```

Misses are cached in `slug_probes` with a 90-day TTL (older misses re-probe
automatically). Zero-job 200s count as misses. Staffing-agency names (Astra
North, Targeted Talent, etc.) are filtered at the candidate stage and never hit
the network.

**Google dorking for Workday boards.** Workday slugs don't surface in the
public-API probes. Use search operators to find active GTA Workday boards, then
feed the URLs to `add`:

```text
site:myworkdayjobs.com "Toronto" "Software Engineer"
```

```bash
jobhunt add https://rbc.wd3.myworkdayjobs.com/en-US/RBC_Careers
```

Vary the role keyword (`"Frontend"`, `"Full Stack"`) or city (`"Mississauga"`,
`"Remote Canada"`). Same trick works for the other hosts — substitute
`site:boards.greenhouse.io`, `site:jobs.lever.co`, `site:jobs.ashbyhq.com`,
`site:apply.workable.com`, etc. Once a Workday tenant is configured, large
enterprise/banking boards (TD, BMO, NVIDIA, Capital One) are scanned with
GTA-targeted `searchText` queries so Toronto roles aren't buried; smaller
Canada-centric boards keep the plain first-100 walk — no extra config.

> **Heads-up:** all three writers (`add`, `config seed --apply`,
> `discover slugs --apply`) write `config.toml` programmatically — any inline
> comments are stripped on write. A `.bak` snapshot is created next to the file.

### Analysis (`analyze`)

Deterministic, LLM-free aggregations over the jobs DB — regex + counters, no
network I/O.

#### `analyze certs` — cert decision tool

Three modes:

```bash
jobhunt analyze certs                          # snapshot frequency (default 25 rows)
jobhunt analyze certs --trend                  # prev vs current 30d windows + Δ% + trend label
jobhunt analyze certs --trend --min-score 55   # fit-filtered: adds Fit column + Verdict
```

Windows bucket by `COALESCE(posted_at, ingested_at)`; `--window-days N` adjusts
(default 30). The Verdict column (only with `--min-score`) classifies each cert
against a small rubric so the decision is one column wide:

| Verdict | Means |
|---|---|
| Strong emerging signal | New in current window, fit_cur ≥ 3 |
| Worth pursuing | Rising 50 %+, fit_cur ≥ 3 |
| Stable staple | Steady demand, fit_cur ≥ 3, top-10 by market presence |
| Marginal | Stable but not top of market |
| Late — diminishing | Falling 50 %+, even if fit_cur ≥ 3 |
| Skip | Fewer than 3 jobs you'd qualify for mention it |
| Wrong direction | Common in the market (cur ≥ 5) but zero fit-eligible jobs |

`--min-score N` joins the `scores` table; match it to your apply threshold
(default 55). The `Potential new certs` section surfaces generic-regex hits not
yet in the curated `_KNOWN` list in `src/jobhunt/analyze/certs.py` — review and
promote real catches by hand.

#### Other `analyze` subcommands

```bash
jobhunt analyze skills --gaps                  # tech tokens over-represented in declined vs accepted JDs
jobhunt analyze employers --hiring-velocity    # post counts per slug; surfaces configured-but-silent boards
jobhunt analyze validators                     # which cover-letter validators fired most — prune over-broad rules
jobhunt analyze response-rate --by score       # interview/response rate per score band (or --by ats)
```

All take `--window-days N`; most take `--top N`. Use `response-rate` after ~20
applications alongside `jobhunt config calibrate` to tune `[pipeline] min_score`.

## Configuration

`~/.config/jobhunt/config.toml` (abridged — full schema in
[src/jobhunt/config.py](src/jobhunt/config.py); run `jobhunt config show`
for your live values):

```toml
[paths]
data_dir       = "/home/you/Apps/jobhunt/data"
db_path        = "/home/you/Apps/jobhunt/data/jobhunt.db"
migrations_dir = "/home/you/Apps/jobhunt/migrations"
kb_dir         = "/home/you/Apps/jobhunt/kb"

[ingest]
user_agent           = "jobhunt/0.1 (+personal-use; you@example.com)"
rate_limit_per_sec   = 1.0
cache_ttl_hours      = 6
max_age_days         = 7      # drop postings older than N days; 0 disables
greenhouse           = []     # board slugs, e.g. "faire"
lever                = []     # board slugs, e.g. "benchsci"
ashby                = []     # board slugs, e.g. "cohere"
smartrecruiters      = []     # case-sensitive company slugs (e.g. "Bosch")
workday              = []     # "tenant:host:site" triples (ingest/workday.py)
workable             = []     # board slugs, e.g. "deliveroo"
recruitee            = []     # board slugs (the `<slug>` in <slug>.recruitee.com)
job_bank_ca          = []     # full RSS URLs from jobbank.gc.ca search results
rss                  = []     # generic employer career-page RSS/Atom URLs
auto_discover        = true   # post-ingest probe new companies + append hits.
                              # Disable for narrow profiles where the GTA
                              # Greenhouse universe (Staff IC / ML / data
                              # platform at well-funded US tech) is mostly
                              # off-target — Adzuna's verified-skill-derived
                              # queries already cover the productive surface.
                              # `--no-discover` skips per-run.
drop_research_titles = false  # opt-in: drop ML scientist / research
                              # engineer / data platform / quant titles at
                              # ingest. Enable for frontend / CMS /
                              # full-stack profiles where these roles never
                              # fit. See `ingest._filter.is_research_title`.

[ingest.adzuna]
# Empty list = auto-derive from kb/profile/verified.json (skills + bullets).
# Populate to override with a verbatim list.
queries          = []
pages            = 3
results_per_page = 50

[gateway]
base_url = "http://localhost:11434/v1"
api_key  = "ollama"

[gateway.tasks]
score  = "qwen3.5:9b"
tailor = "qwen3.5:9b"
cover  = "qwen3.5:9b"
embed  = "nomic-embed-text"

[pipeline]
score_concurrency     = 2
tailor_max_words      = 700
cover_max_words       = 280
cover_retry_attempts  = 3
tailor_retry_attempts = 3
answer_max_words      = 200  # `answer` default word cap
min_score             = 55   # apply / list default floor

[browser]
headed        = true
user_data_dir = "/home/you/Apps/jobhunt/data/browser-profile"

[applicant]
full_name              = "Your Name"
email                  = "you@example.com"
phone                  = ""
linkedin_url           = "https://www.linkedin.com/in/you"
github_url             = "https://github.com/you"
portfolio_url          = "https://you.com"
city                   = "Toronto"
region                 = "Ontario"
country                = "Canada"
work_auth_canada       = true
requires_visa_sponsorship = false
salary_expectation_cad = "50,000 - 90,000 CAD"
years_experience       = 3       # YoE; drives score prompt auto-decline rules
include_senior_roles   = true    # set false to drop Senior/Sr/Lead/Staff/
                                 # Principal/Architect titles at ingest
pronouns               = "he"
work_arrangements      = ["onsite", "hybrid", "remote"]
employment_types       = ["full_time", "contract"]
```

See [Slug acquisition](#slug-acquisition--automatic-with-manual-fallbacks)
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
[AGENTS.md](AGENTS.md) LLM call rules and Post-generation audit rules
for the full mechanism.

## License

Copyright (c) [2026] [Casey Hsu]

Permission is hereby denied :D
