# Job Hunt AI Buddy
 
A local-first CLI LLM for Casey's Toronto-area job hunt. Pulls jobs from public
ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Job Bank Canada,
generic RSS, Adzuna CA), scoped to GTA + 100 km and Remote-Canada postings.
Fit-scores them against the parsed baseline resume using local Ollama models,
drafts a tailored resume and cover letter per role, and assists with form
autofill in the browser. **You submit every application yourself.** The tool
fills the form; it never clicks Submit.

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

ollama pull qwen3.5:9b           # base model — score, tailor, cover
ollama pull nomic-embed-text     # embeddings (reserved for future use)
```

Default model in config is `qwen-custom:latest` — a Modelfile-derived
`qwen3.5:9b` baking in personal prompt context. If you haven't built the
custom variant, set all three `[gateway.tasks]` slots back to `qwen3.5:9b` in
`~/.config/jobhunt/config.toml`. Same VRAM footprint either way. See
[AGENTS.md](AGENTS.md) §Hardware context for the full rationale.

### Ollama systemd settings

The gateway is tuned to a specific server config. Mirror these
(`sudo systemctl edit ollama.service`):

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q5_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_CONTEXT_LENGTH=16384"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

The gateway sends matching per-call values (`num_ctx=16384`, `keep_alive=-1`).
If you change one side, change the other.

## First run

> `config` and `db` are setup-only commands — they're hidden from `--help` after install.

```bash
jobhunt config show       # writes a default config and prints it
jobhunt db init           # creates SQLite schema at data/jobhunt.db
jobhunt convert-resume    # generates kb/profile/* from Resume.docx
```

`scan`, `list`, `apply`, and `analyze` will refuse to run until
`convert-resume` has been executed — they need `kb/profile/verified.json` as
the source of truth.

To start over (drops DB, tailored documents, HTTP cache, browser profile, parsed resume):

```bash
jobhunt db reset          # prompts for 'yes', then re-inits schema
jobhunt convert-resume
```

## Commands

Six user-facing commands. Run `<command> --help` for full flags.

```bash
jobhunt convert-resume                     # parse Resume.docx → kb/profile/
jobhunt scan                               # ingest GTA jobs + score against profile
jobhunt apply <job-id>                     # tailor + cover + autofill (you submit)
jobhunt apply --top N                      # auto-pick N best-fit unapplied jobs (1..20)
jobhunt apply --best                       # interactive pick from top 10
jobhunt apply --url <URL>                  # manual application from a URL
jobhunt list [--week N] [--status ...]     # pipeline view + weekly tracking
jobhunt analyze certs [--top N]            # frequency of certifications in scanned jobs
jobhunt discover slugs [--apply]           # probe Greenhouse/Ashby for ATS slugs
```

### `apply` selection modes

- `apply <job-id>` — single job by id.
- `apply --top N` — N highest-scoring unapplied jobs above `--min-score`
  (default 65). Capped at 10.
- `apply --best` — lists the top 10 candidates and prompts for picks like
  `1,3,7` or `2-5`.
- `apply --url <URL>` — bypass `scan` for a one-off posting. Fetches the page
  in headless Chromium so JS-heavy portals (Workday, Phenom, iCIMS) load
  their JD content. Use `--title` / `--company` if auto-detection misses.
  Escape hatches: `--no-score` skips scoring; `--force-robots` overrides
  the robots.txt check for that single fetch.

Add `--no-browser` to any `apply` invocation to generate tailored docs without
launching Playwright.

Bump status after submitting:

```bash
jobhunt apply --set-status applied      <job-id>
jobhunt apply --set-status interviewing <job-id>
jobhunt apply --set-status rejected     <job-id>
```

The flag must come **before** the job id.

### `discover slugs`

Adzuna ships short JD snippets (~500 chars). Greenhouse and Ashby return full
descriptions, but each employer needs a slug in `config.toml`. `discover slugs`
automates the slug-hunting: it reads distinct company names from your jobs DB,
normalizes each (e.g. `"Konrad Group"` → `konradgroup`, `konrad`), and probes
the public Greenhouse and Ashby APIs.

```bash
jobhunt discover slugs                     # print suggestions (default --limit 100)
jobhunt discover slugs --apply             # also append to config.toml (.bak written)
jobhunt discover slugs --ats greenhouse    # restrict probe targets
jobhunt discover slugs --include-cached    # re-probe past misses
```

Misses are cached in the `slug_probes` table so repeat runs only probe new
companies. Staffing-agency names (Astra North, Targeted Talent, etc.) are
filtered at the candidate stage and never hit the network.

### `list` filters

`--week N` (0=current, 1=last, …), `--status drafted|applied|interviewing|offer|rejected`,
`--min-score N`, `--source greenhouse|lever|ashby|smartrecruiters|workday|job_bank_ca|rss|adzuna_ca`.
Always renders a weekly rollup footer (scanned / declined / per-status counts).

## Daily flow

```bash
jobhunt scan                      # pulls new jobs + scores them
jobhunt list --min-score 70       # see today's high-fit subset
jobhunt apply --best              # pick which to apply to
# Browser opens. You review, click Submit yourself.
jobhunt list --week 0             # weekly pipeline view
```

## Configuration

`~/.config/jobhunt/config.toml`:

```toml
[ingest]
greenhouse      = ["shopify", "1password", "wealthsimple", "faire"]
lever           = ["benchsci", "ada"]
ashby           = ["cohere"]
smartrecruiters = []   # company slugs, e.g. "Bosch", "Visa"
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
```

**Finding ATS slugs:** visit `<company>.com/careers` and look for the
redirect/embed: `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`,
`jobs.ashbyhq.com/<slug>`, `careers.smartrecruiters.com/<Company>`. Workday
is harder — copy the `tenant:host:site` triple from the careers URL (see
`src/jobhunt/ingest/workday.py`).

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
| `kb/prompts/{score,tailor,cover}.md` | Prompts with JSON-schema frontmatter. |
| `~/.config/jobhunt/config.toml` | Sources, models, applicant profile, paths. |
| `~/.config/jobhunt/secrets.toml` | API keys (Adzuna), mode 0600. |
| `data/jobhunt.db` | SQLite — jobs, scores, applications. |
| `data/applications/<job-id>/` | Tailored resume, cover letter, fill-plan.json. |

`data/` is gitignored.

## For maintainers

- [AGENTS.md](AGENTS.md) — conventions, guardrails, project structure. The *how*.
- [PLAN.md](PLAN.md) — design rationale. The *why*.
- [Resume_Tailoring_Instructions.md](Resume_Tailoring_Instructions.md) — honesty rules enforced by the tailor pipeline.

Honesty enforcement is structural (verified-snapshot constraint, schema-bounded
output, post-decode invariants, score clamp, cover validator + retry). See
[AGENTS.md](AGENTS.md) §LLM call rules and §Post-generation audit rules for
the full mechanism.

## License

MIT License

Copyright (c) [2026] [Casey Hsu]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
