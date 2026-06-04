# Jobhunt AI Buddy

A local-first CLI for Casey's Toronto-area job hunt. Pulls jobs from public
ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Workable,
Recruitee, Job Bank Canada, generic RSS, Adzuna CA), scoped to GTA +
100 km and Remote-Canada postings. After each scan, the tool probes public
ATS APIs for slugs of newly-seen companies and auto-appends hits to
`config.toml`, so the next scan pulls deep JDs natively, slug curation is
mostly automatic. Fit-scores them against the parsed baseline resume using local
Ollama models, drafts a tailored resume and cover letter per role, answers
free-form application form questions, and assists with form autofill in the
browser. **You submit every application yourself.** The tool fills the
form. It never clicks Submit.

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

ollama pull qwen3.5:9b           # base model: all LLM tasks
ollama pull nomic-embed-text     # embeddings (reserved for future use)
```

Default model in config is base `qwen3.5:9b` (Q4_K_M). The gateway supplies its
own task prompt and its own options, notably `num_ctx=32768` and
`presence_penalty=0`, so behavior is defined in-repo and no custom Modelfile is
needed. The `num_ctx` pin is essential: these prompts exceed Ollama's 4096
default, and without it they truncate and the model returns prose instead of
JSON. It is set to 32768 because the model stays 100% GPU-resident at 32k on
this card (~5.6 GB), so the headroom is free. See [AGENTS.md](AGENTS.md)
Hardware context for the full rationale, including why the q8_0 build was
rejected (it spills to CPU here).

### Ollama systemd settings

The gateway is tuned to a specific server config. Mirror these
(`sudo systemctl edit ollama.service`):

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_VULKAN=0"
```

`OLLAMA_CONTEXT_LENGTH` is intentionally NOT set: context is owned at the app
level (the gateway's `num_ctx`) so each project sharing this box picks its own
window. The systemd `OLLAMA_KEEP_ALIVE=-1` matches the per-call `keep_alive=-1`
the gateway uses. Rationale and tuning notes live in [AGENTS.md](AGENTS.md)
Hardware context.

## Workflows

The day-to-day is **scan → list → apply**. Everything else is occasional. Drop
your baseline resume at `./Resume.docx` before the first run. Every command and
flag is documented in full under [Commands](#commands). You shouldn't need to
reach for `--help`.

### First run

```bash
jobhunt setup
```

The wizard walks every first-run step in order: init the SQLite DB + run
migrations → confirm `Resume.docx` is in place → parse it into
`kb/profile/verified.json` + markdown sidecars → prompt for applicant defaults
(`years_experience`, `include_senior_roles`, salary, work arrangements,
employment types) → print the resolved config → preview the curated
GTA-employer seed list and offer to apply it. Safe to re-run any time to update
applicant defaults. Each step detects existing state and offers keep/redo.

Manual equivalent, if you'd rather not use the wizard:

```bash
jobhunt config show            # writes a default config and prints it
jobhunt db init                # creates SQLite schema at data/jobhunt.db
jobhunt convert-resume         # generates kb/profile/* from Resume.docx
# hand-edit ~/.config/jobhunt/config.toml to fill [applicant] fields
jobhunt config seed --apply    # primes config with verified GTA-employer slugs
```

`scan`, `list`, `apply`, `answer`, and `analyze` refuse to run until
`convert-resume` has produced `kb/profile/verified.json`. To start over (drops
DB, tailored documents, HTTP cache, browser profile, parsed resume):
`jobhunt db reset` then `jobhunt setup`.

### Daily

```bash
jobhunt scan                       # ingest + score + auto-discover new slugs
jobhunt list --min-score 70        # high-fit subset
jobhunt apply --best               # tailor + draft the day's picks (browser opens; you submit)
jobhunt apply --set-status applied <job-id>   # after you submit each one

# As needed:
jobhunt apply --url https://jobs.example.com/p/12345   # one-off posting, bypass scan
jobhunt answer "Why are you interested in this role?" --job <id>   # free-form form question
jobhunt apply --set-status interviewing <job-id>       # when an interview lands
jobhunt interview-prep <job-id> --stage agency --research
```

A batch `apply --top N` / `apply --best` run prints a one-line summary
afterward: how many drafted / revised / blocked, plus the top warning
categories across the batch. If the scan summary shows `! <slug>: 404 …`, note
it but don't panic. Transient 404s happen, and the weekly reprobe pass is where to
act on them.

### Weekly

```bash
jobhunt list --week 0                          # current-week pipeline rollup
jobhunt list --no-reply --older-than 14d       # applied, no reply, >14d (nudge candidates)
jobhunt config reprobe --prune                 # re-probe configured slugs; prune dead ones
jobhunt analyze response-rate --by score       # interview rate per score band
jobhunt analyze certs --trend --min-score 55   # cert intel + per-cert verdict
jobhunt analyze employers --hiring-velocity    # surfaces configured-but-silent slugs
jobhunt analyze validators                     # which cover-letter validators fired most
```

After ~20 applications, run `jobhunt config calibrate` to see the interview rate
per score band and tune `[pipeline] min_score`.

## Commands

Eight user-facing top-level commands plus the `analyze` and `discover` groups.
`config` and `db` are hidden setup-only internals (documented at the end for
completeness). Ordered below from most- to least-used. Every command also
accepts the global `--debug` (full tracebacks) and `--verbose` / `-v` flags.

### `scan`

Ingests GTA + Remote-Canada postings from every configured source, scores each
against your profile, dedupes across sources, and (by default) auto-discovers
new ATS slugs after ingest.

```bash
jobhunt scan                    # ingest + score + auto-discover slugs
jobhunt scan --max-age-days 30  # widen the freshness window (default 7; 0 disables)
jobhunt scan --no-discover      # skip the post-ingest slug auto-discovery step
jobhunt scan --skip-score       # ingest only; don't score
jobhunt scan --skip-ingest      # score the backlog only; don't ingest
jobhunt scan --limit N          # cap how many jobs to score this run
jobhunt scan --refresh          # bypass the HTTP cache and re-fetch sources
```

Pre-score filters: freshness window (`--max-age-days`), management-title drop,
plus optional senior-title and research/ML-title drops
(`[applicant] include_senior_roles`, `[ingest] drop_research_titles`). A warm-up
call fires before the scoring loop so the first real call doesn't pay cold-load.

### `list`

Pipeline view + weekly rollup. Always renders a footer (scanned / declined /
per-status counts). Rows you've run `apply` on show a `cov=NN%` tag, the
keyword-coverage % from the latest `audit.json`. A `cov < 70%` is a re-tailor
candidate.

```bash
jobhunt list                                # default view (most recent, --limit 20)
jobhunt list --week 0                        # 0=current week, 1=last, …
jobhunt list --min-score 70                  # high-fit subset
jobhunt list --status interviewing           # drafted|applied|interviewing|offer|rejected
jobhunt list --verdict ship                  # audit verdict: ship|revise|block
jobhunt list --source greenhouse             # filter by ingest source (see list below)
jobhunt list --no-reply --older-than 14d     # applied, no recruiter reply, >14d (nudge list)
jobhunt list --limit 50                      # max rows to display
```

`--source` accepts: `greenhouse`, `lever`, `ashby`, `smartrecruiters`,
`workday`, `workable`, `recruitee`, `job_bank_ca`, `rss`, `adzuna_ca`.

### `apply`

Tailors a resume + cover letter for a job, runs the deterministic audit, then
opens a headed browser and fills the form. **You review and click Submit
yourself.** Add `--no-browser` to generate docs only.

Selection modes:

```bash
jobhunt apply <job-id>          # single job by id
jobhunt apply --top N           # N highest-scoring unapplied above --min-score (capped at 10)
jobhunt apply --best            # interactive picker over the top 10 (`1,3,7` or `2-5`)
jobhunt apply --best --include-borderline   # also surface stretch jobs in [min_score-10, min_score)
jobhunt apply --url <URL>       # one-off posting, bypass scan (headless fetch for JS portals)
```

`--min-score` overrides `[pipeline] min_score` (default 55) for `--top`/`--best`.
For `--url`, use `--title` / `--company` if auto-detection misses.
`--description-from-stdin` pipes a JD in directly, `--no-score` skips the score
pass, and `--force-robots` overrides the robots.txt check for that single fetch.

Status / lifecycle updates (the flag comes **before** the job id):

```bash
jobhunt apply --set-status applied      <job-id>   # drafted|applied|interviewing|offer|rejected
jobhunt apply --set-status interviewing <job-id>
jobhunt apply --mark-response   <date>  <job-id>   # record a recruiter reply date
jobhunt apply --mark-interview  <date>  <job-id>   # record an interview date
jobhunt apply --set-outcome     <value> <job-id>   # final outcome
jobhunt apply --recruiter-type  <type>  <job-id>   # internal_recruiter|hiring_manager|external_agency|unknown
```

After a batch run the tool prints a one-line summary (drafted / revised /
blocked + top warning categories). `--set-status interviewing` prints a nudge
pointing at `interview-prep`.

### `answer`

Drafts a response to a free-form application-form question against your verified
profile, under the same honesty rules as cover letters (banned phrases,
defensive gap-volunteering, fabrication watchlist, unverified numbers), up to 3
retries.

```bash
jobhunt answer "Why are you looking for a new role right now?"   # standalone, no JD
jobhunt answer "Why us?" --job adzuna_ca:5730918359              # job-scoped (loads the JD)
jobhunt answer "Years of TypeScript?" --max-words 60            # short-factual
jobhunt answer "Walk me through a project" --max-words 250      # STAR-style
jobhunt answer "Anything else?" --no-save                        # print to stdout only
jobhunt answer "interested in this role" --recall                # search past saved answers
```

The answer prints between separator bars (clean copy-paste target). By default
it's saved to `data/applications/<job-id>/answers/<sha1>.md` (with `--job`) or
`data/answers/<sha1>.md` (standalone). The filename is a sha1 of the question,
so re-running the same question overwrites the same file. `--recall` treats the
argument as a phrase and lists past saved answers whose question text contains
it (case-insensitive).

### `add`

The daily slug-acquisition driver. Paste any recognized career-page or
job-posting URL. The tool parses the ATS, probes once to confirm, and appends to
`config.toml`.

```bash
jobhunt add https://boards.greenhouse.io/faire
jobhunt add https://jobs.ashbyhq.com/cohere
jobhunt add https://rbc.wd3.myworkdayjobs.com/en-US/RBC_Careers
jobhunt add <URL> --skip-probe   # append without the confirmation probe
```

Recognized hosts: `boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`,
`jobs.smartrecruiters.com`, `*.wd*.myworkdayjobs.com`, `apply.workable.com`,
`*.workable.com`, `*.recruitee.com`. iCIMS URLs are recognized but exit with
"coming soon" (no adapter yet). After `apply --url <careers-page>`, the tool
prints an `add` suggestion if the URL belongs to an unconfigured ATS.

> **Heads-up:** `add`, `config seed --apply`, and `discover slugs --apply` all
> write `config.toml` programmatically, so inline comments are stripped on write.
> A `.bak` snapshot is created next to the file each time.

### `interview-prep`

When an application converts to an interview, draft a prep doc anchored on your
verified profile and the cached JD.

```bash
jobhunt interview-prep <job-id>                        # default --stage agency
jobhunt interview-prep <job-id> --stage hiring_manager # hiring-manager round
jobhunt interview-prep <job-id> --stage assessment     # assessment / final round
jobhunt interview-prep <job-id> --research             # fetch JD URL + company root
jobhunt interview-prep <job-id> --research --refresh-research   # ignore cached research
jobhunt interview-prep <job-id> --research --force-robots
jobhunt interview-prep <job-id> --recruiter-type external_agency   # tune emphasis
jobhunt interview-prep <job-id> --no-llm               # skeleton-only (debug)
```

Output: `data/interview-prep/<job-id-safe>.md` (re-runs overwrite). Hybrid
generation: a deterministic skeleton (header, comp heads-up, pre-call
checklist, footer) wraps an LLM-drafted middle (role decode, anchors, likely
questions with answer beats, questions to ask back, honest gaps with reframes).
Anchors must trace to verified facts, under the same honesty rules + 3 retries as covers.

### `analyze`

Deterministic, LLM-free aggregations over the jobs DB: regex + counters, no
network I/O. Five subcommands:

```bash
jobhunt analyze certs                          # snapshot cert frequency (default --top 25)
jobhunt analyze certs --trend                  # prev vs current 30d windows + Δ% + trend label
jobhunt analyze certs --trend --min-score 55   # fit-filtered: adds Fit column + Verdict
jobhunt analyze skills --gaps                  # tech tokens over-represented in declined vs accepted JDs
jobhunt analyze employers --hiring-velocity    # post counts per slug; surfaces silent boards
jobhunt analyze response-rate --by score       # interview/response rate per score band (or --by ats)
jobhunt analyze validators                     # which cover-letter validators fired most
```

Common flags: `--window-days N` (most subcommands, default 30) and `--top N`
(`certs`, `skills`, `validators`). `analyze certs --min-score N` joins the
`scores` table and adds a per-cert Verdict column (`Worth pursuing` / `Skip` /
`Wrong direction` / …) from a frozen, audit-traceable rubric. Match it to your
apply threshold. The `Potential new certs` section surfaces generic-regex hits
not yet in the curated `_KNOWN` list (`src/jobhunt/analyze/certs.py`) for manual
promotion. Use `response-rate` after ~20 applications alongside
`jobhunt config calibrate` to tune `[pipeline] min_score`.

### `convert-resume`

Parses `./Resume.docx` into `kb/profile/verified.json` plus markdown sidecars
(`resume.md`, `skills.md`, `work-history.md`, `education.md`, and `projects.md`
when the resume has a PROJECTS section). `Resume.docx` is the single source of
truth. Re-run after editing it.

```bash
jobhunt convert-resume                 # parse ./Resume.docx
jobhunt convert-resume --docx path/to/Resume.docx
```

If a line cannot be classified (a skills line not in `Label: items` form, an
unrecognized skill-section label, or a project bullet before any project
header), `convert-resume` prints a `parse warnings` block to stderr and
continues. The warned line is reported, not silently dropped.

### `setup`

Guided first-run wizard. See [First run](#first-run) for the full step list. No
flags. Safe to re-run any time to update applicant defaults. Each step detects
existing state and offers keep/redo.

### `discover slugs` (legacy)

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
automatically). Zero-job 200s count as misses. Staffing-agency names are
filtered at the candidate stage and never hit the network.

**Google dorking for Workday boards.** Workday slugs don't surface in the
public-API probes. Use search operators to find active GTA Workday boards, then
feed the URLs to `add`:

```text
site:myworkdayjobs.com "Toronto" "Software Engineer"
```

Vary the role keyword (`"Frontend"`, `"Full Stack"`) or city (`"Mississauga"`,
`"Remote Canada"`). The same trick works for the other hosts: substitute
`site:boards.greenhouse.io`, `site:jobs.lever.co`, `site:jobs.ashbyhq.com`,
`site:apply.workable.com`, etc. Once a Workday tenant is configured, large
enterprise/banking boards (TD, BMO, NVIDIA, Capital One) are scanned with
GTA-targeted `searchText` queries so Toronto roles aren't buried. Smaller
Canada-centric boards keep the plain first-100 walk, no extra config.

### `config` (hidden internal)

Setup-only, hidden from `--help` after install. `config seed --apply` is part of
onboarding.

```bash
jobhunt config seed --preview   # see what the curated GTA-employer seed list would add
jobhunt config seed --apply     # additively merge kb/seeds/gta-employers.toml into config.toml
jobhunt config reprobe          # re-probe every configured slug; print live vs stale
jobhunt config reprobe --prune  # remove stale slugs (confirms first unless --force)
jobhunt config show             # print the resolved live config (writes a default if absent)
jobhunt config path             # print the config file path
jobhunt config calibrate        # interview rate per score band (use after ~20 applications)
```

The seed list is read-only at runtime and only updated through
`scripts/verify_seeds.py`, which probe-checks every entry before commit, so dead
slugs don't ship. `reprobe` skips Workday (its CXS handshake isn't a cheap probe).

### `db` (hidden internal)

Setup-only, hidden from `--help`.

```bash
jobhunt db init             # create the SQLite schema at data/jobhunt.db
jobhunt db migrate          # run pending migrations
jobhunt db reset            # wipe DB + tailored docs + cache + browser profile + kb/profile, then re-init
jobhunt db reset --force    # skip the confirmation prompt
```

## Configuration

`~/.config/jobhunt/config.toml` (abridged, full schema in
[src/jobhunt/config.py](src/jobhunt/config.py), run `jobhunt config show`
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
job_bank_ca          = []     # full jobbank.gc.ca HTML *search* URLs, one per role query,
                              # e.g. ".../jobsearch/jobsearch?searchstring=software+developer
                              # &locationstring=Toronto%2C+ON&fage=7&sort=D" (RSS is dead;
                              # the adapter scrapes results + GTA-filters client-side, and
                              # honors the site's Crawl-delay: 5)
rss                  = []     # generic employer career-page RSS/Atom URLs
auto_discover        = true   # post-ingest probe new companies + append hits.
                              # Disable for narrow profiles where the GTA
                              # Greenhouse universe (Staff IC / ML / data
                              # platform at well-funded US tech) is mostly
                              # off-target. Adzuna's verified-skill-derived
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
thin_jd_score_cap     = 70   # confidence ceiling for signal-poor (short) JDs
thin_jd_chars         = 800  # a JD shorter than this is treated as signal-poor

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

See the [`add`](#add) / [`discover slugs`](#discover-slugs-legacy) /
[`config`](#config-hidden-internal) commands for filling in the ATS slug lists.
The full Pydantic schema with every default lives in
[src/jobhunt/config.py](src/jobhunt/config.py).

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
| `data/jobhunt.db` | SQLite: jobs, scores, applications, slug_probes. |
| `data/applications/<job-id>/` | Tailored resume, cover letter, `audit.json`, `fill-plan.json`, `answers/`. |
| `data/answers/<sha1>.md` | Standalone (non-job-scoped) answer artifacts. |
| `data/interview-prep/<job-id>.md` | Interview prep docs. |
| `data/cache/` | Cached raw HTTP responses (TTL-based). |

`data/` is gitignored.

## For maintainers

This repo carries four agent-facing docs. Edit them in this order. They
cite each other and stay in sync via the cross-tool `AGENTS.md` convention.

- [AGENTS.md](AGENTS.md): guardrails, conventions, project structure,
  pipeline rules. The *how*. Source of truth for any agent (Claude Code,
  Cursor, Codex, Aider) working in this repo.
- [PLAN.md](PLAN.md): design rationale. The *why*. Goals, model choice,
  honesty-enforcement layers, sources, success criteria.
- [IMPLEMENT.md](IMPLEMENT.md): execution engine. Phase-by-phase task
  breakdown, progress checkboxes, current state.
- [CLAUDE.md](CLAUDE.md): tiny stub that `@`-imports AGENTS.md so
  Claude Code's auto-load works. Don't edit it, edit AGENTS.md.
- [Resume_Tailoring_Instructions.md](Resume_Tailoring_Instructions.md):
  honesty rules enforced by the tailor pipeline. Bucket placements,
  things Casey hasn't done, when to tell Casey "no".

Honesty enforcement is structural (verified-snapshot constraint,
schema-bounded output, post-decode invariants, score clamp, cover and
tailor validators + retry, resume↔cover alignment check). See
[AGENTS.md](AGENTS.md) LLM call rules and Post-generation audit rules
for the full mechanism.

## License

Copyright (c) 2026 Casey Hsu. All rights reserved. No license is granted at this time