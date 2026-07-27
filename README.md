# Jobhunt AI Buddy

A local-first CLI that runs a job hunt end to end: ingest, fit-scoring,
tailored documents, and assisted form-fill. Pulls jobs from nine ATS
integrations plus generic RSS (Greenhouse, Lever, Ashby, SmartRecruiters,
Workday, Workable, Recruitee, Job Bank Canada, Adzuna CA, and any RSS feed),
scoped by default to GTA + 100 km and Remote-Canada postings — both
configurable. After each scan, the tool probes public
ATS APIs for slugs of newly-seen companies and auto-appends hits to
`config.toml`, so the next scan pulls deep JDs natively and slug curation is
mostly automatic. Fit-scores them against the parsed baseline resume using local
Ollama models, drafts a tailored resume and cover letter per role, answers
free-form application form questions, and assists with form autofill in the
browser. **You submit every application yourself.** The tool fills the
form. It never clicks Submit.

Everything runs locally. No resume or job data leaves your hardware. Zero
cloud LLM calls in the runtime path.

Applications made *outside* the pipeline (LinkedIn Easy Apply, Indeed,
referrals, recruiter outreach) are tracked too: `jobhunt track` logs them
without scraping — paste the posting, tag the channel — and
`jobhunt analyze funnel` shows which channel actually converts.

## What it looks like

```
$ jobhunt list
ready to apply: 19  |  drafted, not submitted: 0  |  no reply >14d: 0

showing 10 job(s)
  [ 82] [—            ] Software Developer, AI Platform Foundations @ wealthsimple
           ashby | Remote (Canada) | ashby:wealthsimple:a04d491e-d747-4ad8-ab7f-82e9cd39089e
           https://jobs.ashbyhq.com/wealthsimple/a04d491e-d747-4ad8-ab7f-82e9cd39089e
  [ 82] [—            ] Product Engineer - Retailer Experience & Growth @ faire
           greenhouse | Kitchener-Waterloo, ON; Toronto, ON | greenhouse:faire:8603123002
           https://boards.greenhouse.io/faire/jobs/8603123002?gh_jid=8603123002
  [ 64] [—            ] Senior Software Developer, Brokerage @ wealthsimple
           ashby | Toronto Headquarters | ashby:wealthsimple:fec01150-fed6-4158-9e94-e59328d79533
           https://jobs.ashbyhq.com/wealthsimple/fec01150-fed6-4158-9e94-e59328d79533
  [ 58] [—            ] Observability Architect @ geotab
           greenhouse | Remote - Canada | greenhouse:geotab:5281780008
           https://job-boards.greenhouse.io/geotab/jobs/5281780008

2026-W31: | scanned=72 | declined=41 | drafted=0 | applied=0 | interviewing=0 | offer=0 | rejected=0 | withdrawn=0
```

The bracketed number is the fit score; the second bracket is application
status. The weekly funnel line is the same data `jobhunt analyze funnel`
breaks down by channel.

## Architecture

Six stages over one SQLite database. Each stage is its own command, so any
stage can be re-run without repeating the ones before it.

```
ingest ──▶ discover ──▶ score ──▶ tailor ──▶ audit ──▶ autofill
```

| Stage | Module | Responsibility |
| --- | --- | --- |
| `ingest` | `ingest/` | Nine ATS integrations plus generic RSS, over async httpx with per-host rate limiting |
| `discover` | `discover/` | Deterministic URL → `(ats, slug, site, host)` parsing, async ATS probing, auto-appends new slugs to `config.toml` |
| `score` | `pipeline/score.py` | Fit score against the verified resume snapshot |
| `tailor` | `pipeline/tailor.py`, `cover.py`, `answer.py` | Per-role resume, cover letter, form answers |
| `audit` | `pipeline/audit.py`, `cover_validate.py` | Deterministic. No LLM in the QA path. |
| `autofill` | `browser/` | Per-ATS Playwright handlers + generic fallback. Fills; never clicks Submit. |

Cutting across all of them: **`gateway/` is the only place the model is
reached.** Every LLM call goes through one `complete_json` entry point
(`POST /api/chat` with `format=<schema>`), and every call is schema-bounded —
there is no free-text completion path in the runtime.

**The load-bearing setting is `num_ctx=32768`,** pinned app-side in
`gateway.client._DEFAULT_OPTIONS` rather than in the Ollama server environment.
These prompts run ~6k tokens and Ollama's default context is 4096, so without an
explicit `num_ctx` the prompt silently truncates, the JSON-schema instruction
falls off the end, and the model returns prose instead of JSON. The failure mode
looks like a parser bug and isn't one. Context is owned at the app level so
several projects can share one Ollama box, each picking its own window.

## Honesty enforcement

The hard problem in a resume generator isn't fluency — it's stopping the model
from inventing experience. Six mechanisms, all structural rather than
prompt-based, so none of them depend on the model choosing to comply:

1. **Verified-snapshot constraint.** Generation reads from a verified profile
   snapshot — skills, work history, summary — not from the job description.
   The JD selects and orders; it never supplies content.
2. **Schema-bounded output.** Every call declares a JSON schema, so the model
   cannot return a shape the pipeline didn't ask for.
3. **Post-decode invariants.** `pipeline.tailor._enforce_no_fabrication`
   re-checks the decoded object against the snapshot. A skill that isn't an
   identity-subset of a verified skill is rejected *after* the model has spoken
   — the guarantee doesn't rest on the prompt.
4. **Score clamp.** `pipeline.score` re-partitions the model's claimed
   must-haves against the verified blob, so a requirement can't be counted as
   matched on the model's say-so. Skipped below 3 must-haves, so signal-poor
   postings aren't scored on noise.
5. **Validators with retry.** `pipeline.cover_validate` catches banned phrases,
   defensive patterns, and unverified numbers. A failure re-runs the call at
   `temperature=0` — recovery, not relaxation: every attempt faces the same
   invariants, and a run that can't satisfy them declines rather than degrades.
6. **Resume↔cover alignment.** `pipeline.audit._alignment_flags` cross-checks
   the two generated documents against each other, catching claims that are
   individually valid but mutually inconsistent.

The eval harness carries an off-lane control fixture that is *supposed* to
decline at the score stage (`scripts/eval_tailor.py`). A run that produces
confident output for it is a regression, not a success.

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
cd Jobhunt

uv sync
source .venv/bin/activate        # puts `jobhunt` on PATH; or prefix commands with `uv run`
playwright install chromium

ollama pull qwen3.5:9b           # base model: all LLM tasks
ollama pull nomic-embed-text     # embeddings (reserved for future use)
```

Default model in config is base `qwen3.5:9b` (Q4_K_M). The gateway supplies its
own task prompt and its own options, so behavior is defined in-repo and no
custom Modelfile is needed. Q4_K_M stays 100% GPU-resident at ~5.6 GB on a 10 GB
card; the `q8_0` build was evaluated and rejected because it spills to CPU with
no quality gain. The `num_ctx=32768` pin is load-bearing — see
[Architecture](#architecture) above for why, and [AGENTS.md](AGENTS.md)
Hardware context for the full rationale.

### Ollama systemd settings

The gateway is tuned to a specific server config. Mirror these
(`sudo systemctl edit ollama.service`):

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q4_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=10m"
```

`OLLAMA_CONTEXT_LENGTH` is intentionally NOT set — context is owned at the app
level (the gateway's `num_ctx`) so each project sharing this box picks its own
window.

## Workflows

The day-to-day is **scan → list → apply**. Everything else is occasional. Drop
your baseline resume in the repo root before the first run — any root-level
`.docx` with "resume" in the filename is picked up, so `Baseline_Resume.docx`
and `Jane_Dev_Resume.docx` both work. The search is non-recursive on purpose,
so a generated copy under `data/` can never become its own source. Pass
`--docx <path>` to override. This README covers the common path. Use
`jobhunt --help` and `jobhunt <command> --help` for the full flag reference.

### First run

```bash
jobhunt setup
```

The wizard walks every first-run step in order: init the SQLite DB + run
migrations → locate your baseline resume and confirm the choice → parse it into
`kb/profile/verified.json` + markdown sidecars → prompt for applicant defaults
(`years_experience`, `include_senior_roles`, salary, work arrangements,
employment types) → print the resolved config → preview the curated
GTA-employer seed list and offer to apply it. Safe to re-run any time to update
applicant defaults. Each step detects existing state and offers keep/redo.

Manual equivalent, if you'd rather not use the wizard:

```bash
jobhunt config show            # writes a default config and prints it
jobhunt db init                # creates SQLite schema at data/jobhunt.db
jobhunt convert-resume         # generates kb/profile/* from your baseline resume
# check ~/.config/jobhunt/config.toml — convert-resume backfills [applicant]
# from the resume contact line, but only fills fields that are still empty
jobhunt config seed --apply    # primes config with verified GTA-employer slugs
```

`scan`, `list`, `apply`, `answer`, `resume`, and `analyze` refuse to run until
`convert-resume` has produced `kb/profile/verified.json`. To start over (drops
DB, tailored documents, HTTP cache, interview-prep docs, saved answers,
browser profile, parsed resume): `jobhunt db reset` then `jobhunt setup`.

> **`db reset` removes all of `kb/profile/`, not only the generated files.**
> `convert-resume` regenerates `verified.json` and the five markdown sidecars,
> but any hand-authored file kept in that directory — `verified-notes.md` and
> `work-long-form.md` in this checkout — is gitignored, so a reset destroys it
> with nothing to restore from. Back the directory up before resetting.
> `db reset` also leaves `data/resumes/` in place, so lane resumes generated
> from the previous profile survive; re-run `jobhunt resume --focus <lane>`
> after re-parsing, or the stale copies stay on disk.

### Daily

```bash
jobhunt scan                       # ingest + score + auto-discover new slugs
jobhunt list                       # action board + top unapplied targets
jobhunt list --min-score 70        # high-fit subset
jobhunt apply --best               # tailor + draft the day's picks (browser opens; you submit)
jobhunt apply --set-status applied <job-id>   # after you submit each one

# As needed:
jobhunt apply --url https://jobs.example.com/p/12345   # one-off posting, bypass scan
jobhunt answer "Why are you interested in this role?" --job <id>   # free-form form question
jobhunt apply --set-status interviewing <job-id>       # when an interview lands
jobhunt interview-prep <job-id> --stage agency --research

# Applied somewhere outside the pipeline (LinkedIn / Indeed / referral)?
# Copy the job page (Ctrl-A on the posting), then:
jobhunt track applied --channel linkedin --paste       # paste, Ctrl-D to finish
jobhunt track response opentable --recruiter-type internal_recruiter
jobhunt track interview opentable --when 2026-07-24
jobhunt track outcome opentable rejected
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
jobhunt track sweep                            # silent >21d; --apply marks them ghosted
jobhunt config reprobe --prune                 # re-probe configured slugs; prune dead ones
jobhunt analyze funnel --by channel            # applied → response → interview → offer per channel
jobhunt analyze response-rate --by score       # interview rate per score band (also --by channel)
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
| `resume` | Regenerate the lane base resumes for manual channels | `jobhunt resume`, `jobhunt resume --focus ai` |
| `track` | Log + update applications made outside the pipeline (no LLM) | `jobhunt track applied --channel linkedin --paste`, `jobhunt track response <company>` |
| `add` | Add an ATS source from a career URL | `jobhunt add <URL>` |
| `analyze` | Run deterministic job-search reports | `jobhunt analyze certs`, `jobhunt analyze employers` |
| `convert-resume` | Rebuild `kb/profile/` from the baseline resume | `jobhunt convert-resume` |
| `discover slugs` | Legacy slug discovery over past scan rows | `jobhunt discover slugs` |

Hidden maintenance groups are still callable:

```bash
jobhunt config show
jobhunt config seed --apply
jobhunt config reprobe --prune
jobhunt db gc                  # reconcile data/applications/ with the DB
jobhunt db gc --adopt --prune  # recover orphaned docs, delete empty shells
jobhunt db reset
```

`db gc` diffs `data/applications/` against the `applications` table and sorts
what it finds: **adoptable** dirs hold rendered `.docx` files with no row (real
work that was generated and then lost — `--adopt` recovers them as `drafted`),
**stale** dirs hold no rendered docs (`--prune` deletes them), and **blocked**
dirs are left alone because a `block` verdict writing `audit.json` with no row
is the correct outcome, not an orphan. Bare `gc` only reports.

Common patterns:

```bash
jobhunt apply --set-status applied <job-id>
jobhunt apply --mark-response <date> --recruiter-type external_agency <job-id>
jobhunt apply --set-status interviewing <job-id>
jobhunt interview-prep <job-id> --stage hiring_manager --research
jobhunt apply --url <URL> --title "Role" --company "Company"
jobhunt apply --url <URL> --stdin --title "Role" --company "Company"
jobhunt track applied --channel linkedin --paste          # paste the copied LinkedIn page
jobhunt track applied --channel indeed --no-jd --title "Role" --company "Co" --when 2026-06-02
jobhunt track outcome "company fragment" ghosted
jobhunt analyze funnel --by channel
```

Notes:

- Anywhere a `<job-id>` is accepted, a company or title fragment works too:
  `jobhunt apply shopify`, `jobhunt interview-prep opentable`,
  `jobhunt track response faire`. An exact id always wins; an ambiguous
  fragment errors and lists the candidates rather than guessing. The fragment
  path skips declined postings, so a declined job needs its full id.
- `apply` fills forms, but you review and submit manually.
- If the submit prompt is answered `no` or cancelled, `apply` records a
  `drafted` row and keeps the job eligible for another apply run. Choose
  `withdrawn` only when you want to remove it from default targets.
- Bare `jobhunt list` prints an action board above the rows — *ready to apply*
  (scored at or above `[pipeline] min_score`, no application row, not
  declined), *drafted, not submitted*, and *no reply >14d* — with the command
  for each non-empty queue. Any explicit filter flag suppresses it.
- `apply --best` opens an interactive picker over the top scored jobs.
- `apply --url` creates a tracked `manual:` job for a one-off posting.
- `--stdin` is the paste-JD path for pages that do not render cleanly.
- `track` never runs an LLM and never scrapes: `--paste` parses a page YOU
  copied. Pasting the full page (with "About the job") stores the JD, which
  `interview-prep` needs later — worth the extra Ctrl-A. `--no-jd` backfills
  expired postings as tracking-only rows.
- `track sweep` is the only thing that records a *non*-response. Without it,
  applications sit in `applied` forever and `analyze funnel` reads permanent
  silence as "still pending", so response rates read low for the wrong reason.
  Run it weekly; bare `sweep` only reports.
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
discover_backlog_ceiling = 40 # skip the post-ingest discovery probe while this
                              # many scored, unapplied, non-declined jobs are
                              # already queued (the "ready to apply" count on
                              # `jobhunt list`). Widening intake past this point
                              # produces candidates nothing consumes. 0 disables
                              # the gate. Composes with auto_discover: false
                              # there still means "never probe".
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
| `*Resume*.docx` | Source-of-truth resume. Hand-edited. Any root-level `.docx` with "resume" in the name is found automatically; a name containing "baseline" wins, then newest. Override with `convert-resume --docx <path>`. |
| `kb/profile/verified.json` | Structured facts emitted by `convert-resume`. |
| `kb/policies/tailoring-rules.md` | Hard rules (no fabrication, ATS-safe, auto-decline). Injected into prompts; feeds the score prompt hash. |
| `kb/policies/authoring.md` | Agent-facing resume-authoring policy. Not injected. |
| `kb/profile/verified-notes.md` | Long-form claimability notes: bucket placements, quantified outcomes, what the candidate has *not* done. Gitignored, agent-reference only. |
| `kb/profile/work-long-form.md` | Long-form work/project/education knowledge base. Gitignored, agent-reference only. |
| `kb/prompts/{score,tailor,cover,answer}.md` | Prompts with JSON-schema frontmatter. |
| `kb/lanes/{ai-automation,cms-ecommerce}.md` | Pseudo-JD briefs for `jobhunt resume` lane base resumes. |
| `kb/seeds/gta-employers.toml` | Curated verified ATS slugs (imported by `config seed`). |
| `~/.config/jobhunt/config.toml` | Sources, models, applicant profile, paths. |
| `~/.config/jobhunt/secrets.toml` | API keys (Adzuna), mode 0600. |
| `data/jobhunt.db` | SQLite: jobs, scores, applications, slug_probes. |
| `data/applications/<job-id>/` | Tailored resume, cover letter, `audit.json`, `fill-plan.json`, `answers/`. |
| `data/answers/<sha1>.md` | Standalone (non-job-scoped) answer artifacts. |
| `data/resumes/` | Lane base resumes (`jobhunt resume`) + their tailored JSON. |
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
- `IMPLEMENT.md`: execution engine. Phase-by-phase task breakdown, progress
  checkboxes, current state. **Untracked** (`.gitignore`) — it is a working
  file for whoever is mid-task, not part of a clone. A fresh checkout has no
  `IMPLEMENT.md`; the next agent to plan work creates one.
- [kb/policies/authoring.md](kb/policies/authoring.md): agent-facing resume
  authoring policy — inputs to demand, the tailoring workflow, what may be
  adjusted, the pre-delivery pitfall audit. Not prompt-injected.
- [kb/README.md](kb/README.md): map for the tracked knowledge base.
- [kb/policies/tailoring-rules.md](kb/policies/tailoring-rules.md):
  prompt-injectable mirror of the tailoring rules.

The six honesty-enforcement mechanisms are summarized under
[Honesty enforcement](#honesty-enforcement) above. See [AGENTS.md](AGENTS.md)
LLM call rules and Post-generation audit rules for the full mechanism, and
[PLAN.md](PLAN.md) for the rationale behind each layer.

## License

Copyright (c) 2026 Casey Hsu. All rights reserved. No license is granted at this time
