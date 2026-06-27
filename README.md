# Jobhunt AI Buddy

A local-first CLI for Casey's Toronto-area job hunt. Pulls jobs from public
ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Workable,
Recruitee, Job Bank Canada, generic RSS, Adzuna CA), scoped to GTA +
100 km and Remote-Canada postings. After each scan, the tool probes public
ATS APIs for slugs of newly-seen companies and auto-appends hits to
`config.toml`, so the next scan pulls deep JDs natively and slug curation is
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
window.

## Workflows

The day-to-day is **scan → list → apply**. Everything else is occasional. Drop
your baseline resume at `./Baseline_Resume.docx` before the first run. This
README covers the common path. Use `jobhunt --help` and
`jobhunt <command> --help` for the full flag reference.

### First run

```bash
jobhunt setup
```

The wizard walks every first-run step in order: init the SQLite DB + run
migrations → confirm `Baseline_Resume.docx` is in place → parse it into
`kb/profile/verified.json` + markdown sidecars → prompt for applicant defaults
(`years_experience`, `include_senior_roles`, salary, work arrangements,
employment types) → print the resolved config → preview the curated
GTA-employer seed list and offer to apply it. Safe to re-run any time to update
applicant defaults. Each step detects existing state and offers keep/redo.

Manual equivalent, if you'd rather not use the wizard:

```bash
jobhunt config show            # writes a default config and prints it
jobhunt db init                # creates SQLite schema at data/jobhunt.db
jobhunt convert-resume         # generates kb/profile/* from Baseline_Resume.docx
# hand-edit ~/.config/jobhunt/config.toml to fill [applicant] fields
jobhunt config seed --apply    # primes config with verified GTA-employer slugs
```

`scan`, `list`, `apply`, `answer`, and `analyze` refuse to run until
`convert-resume` has produced `kb/profile/verified.json`. To start over (drops
DB, tailored documents, HTTP cache, interview-prep docs, saved answers,
browser profile, parsed resume): `jobhunt db reset` then `jobhunt setup`.

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
jobhunt analyze employers                      # surfaces configured-but-silent slugs
jobhunt analyze validators                     # which cover-letter validators fired most
```

After ~20 applications, run `jobhunt config calibrate` to see the interview rate
per score band and tune `[pipeline] min_score`.

## Commands

Use `jobhunt --help` for the top-level menu and `jobhunt <command> --help` for
the full option list. The commands below are the ones you usually need.

| Command | Use it for | Common forms |
|---|---|---|
| `setup` | First run and applicant defaults | `jobhunt setup` |
| `scan` | Ingest, score, and auto-discover sources | `jobhunt scan`, `jobhunt scan --limit N` |
| `list` | Find targets and inspect pipeline status | `jobhunt list`, `jobhunt list --applied`, `jobhunt list --week 0` |
| `apply` | Tailor docs and open the browser autofill flow | `jobhunt apply <job-id>`, `jobhunt apply --best`, `jobhunt apply --url <URL>` |
| `answer` | Draft a form-question response | `jobhunt answer "Question" --job <job-id>` |
| `interview-prep` | Draft an interview prep note | `jobhunt interview-prep <job-id> --research` |
| `add` | Add an ATS source from a career URL | `jobhunt add <URL>` |
| `analyze` | Run deterministic job-search reports | `jobhunt analyze certs`, `jobhunt analyze employers` |
| `convert-resume` | Rebuild `kb/profile/` from the baseline resume | `jobhunt convert-resume` |
| `discover slugs` | Legacy slug discovery over past scan rows | `jobhunt discover slugs` |

Hidden maintenance groups are still callable:

```bash
jobhunt config show
jobhunt config seed --apply
jobhunt config reprobe --prune
jobhunt db reset
```

Common patterns:

```bash
jobhunt apply --set-status applied <job-id>
jobhunt apply --mark-response <date> --recruiter-type external_agency <job-id>
jobhunt apply --set-status interviewing <job-id>
jobhunt interview-prep <job-id> --stage hiring_manager --research
jobhunt apply --url <URL> --title "Role" --company "Company"
jobhunt apply --url <URL> --stdin --title "Role" --company "Company"
```

Notes:

- `apply` fills forms, but you review and submit manually.
- `apply --best` opens an interactive picker over the top scored jobs.
- `apply --url` creates a tracked `manual:` job for a one-off posting.
- `--stdin` is the paste-JD path for pages that do not render cleanly.
- `analyze` is deterministic. It uses regex and counters, not an LLM.
- `add`, `config seed --apply`, and `discover slugs --apply` rewrite
  `config.toml` and create a `.bak` snapshot.

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
drop_non_engineering_titles = true
                              # default-on: drop clearly non-engineering
                              # functions before scoring. A dev/eng title
                              # signal always wins.

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
answer = "qwen3.5:9b"
embed  = "nomic-embed-text"

[pipeline]
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

Use `jobhunt add`, `jobhunt discover slugs`, and `jobhunt config` to fill in
the ATS slug lists.
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
| `Baseline_Resume.docx` | Source-of-truth resume. Hand-edited. |
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

### Local checks

Run the focused test first when you touch one module, then run the broader gates
before handoff:

```bash
uv run pytest
uv run ruff check
uv run mypy src
```

### Quality harnesses

Two manual, live-Ollama scripts live in `scripts/` and stay out of CI.
`scripts/bench_models.py` compares candidate models head-to-head across the
LLM task slots. `scripts/eval_tailor.py` runs the production score, tailor,
cover, and audit pipeline over the fixed golden JD set in
`tests/fixtures/golden/` and prints per-JD score, retry attempts, audit
coverage, verdict, and fired validator rule ids. Run it before and after any
prompt or model change so tailoring quality is measured instead of guessed.
The `offlane-embedded-firmware` fixture is a control: it should decline at
the score step, and a run where it ships is itself a regression signal.

```bash
uv run python scripts/eval_tailor.py                       # full golden set
uv run python scripts/eval_tailor.py --only shopify-developer
```

This repo uses the 4-pillar documentation map from `AGENTS.md`, plus a few
project-only references. Keep them in sync via the cross-tool `AGENTS.md`
convention.

- [AGENTS.md](AGENTS.md): guardrails, conventions, project structure,
  pipeline rules. The *how*. Source of truth for agents working in this repo.
- [PLAN.md](PLAN.md): design rationale. The *why*. Goals, model choice,
  honesty-enforcement layers, sources, success criteria.
- [README.md](README.md): install, usage, and maintainer entry point.
- [IMPLEMENT.md](IMPLEMENT.md): execution engine. Phase-by-phase task
  breakdown, progress checkboxes, current state.
- [Resume_Tailoring_Instructions.md](Resume_Tailoring_Instructions.md):
  honesty rules enforced by the tailor pipeline. Bucket placements,
  things Casey hasn't done, when to tell Casey "no".
- [WORK.md](WORK.md): long-form work, projects, and education knowledge base
  for human and agent resume work.
- [kb/README.md](kb/README.md): map for the tracked knowledge base.
- [kb/policies/tailoring-rules.md](kb/policies/tailoring-rules.md):
  prompt-injectable mirror of the tailoring rules.

Honesty enforcement is structural (verified-snapshot constraint,
schema-bounded output, post-decode invariants, score clamp, cover and
tailor validators + retry, resume↔cover alignment check). See
[AGENTS.md](AGENTS.md) LLM call rules and Post-generation audit rules
for the full mechanism.

## License

Copyright (c) 2026 Casey Hsu. All rights reserved. No license is granted at this time
