# jobhunt

A local-first CLI for Casey's Toronto-area job hunt. Pulls jobs from public ATS
APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Job Bank Canada,
generic RSS, Adzuna CA) scoped to GTA + 100 km and Remote-Canada postings,
fit-scores them against the parsed baseline resume using local Ollama models,
drafts a tailored resume and cover letter per role, and assists with form
autofill in the browser. **You submit every application yourself.** The tool
fills the form; it never clicks Submit.

Everything runs locally. No resume or job data leaves your hardware. Zero
cloud LLM calls in the runtime path.

## Non-goals (deliberate)

- **No LinkedIn or Indeed scraping.** Public ATS APIs only.
- **No bot-submitted applications.** Human-in-the-loop on every submission.
- **No auto-account creation** on employer sites.
- **No stored employer credentials.** If a site needs login, you log in manually.

## Requirements

- Linux or macOS (developed on Arch Linux)
- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) for dependency management
- [Ollama](https://ollama.com) running locally (default `http://localhost:11434`)
- ~10 GB VRAM (`qwen-custom:latest` — a Modelfile-derived `qwen3.5:9b` — lands at ~9 GB resident at `num_ctx=16384` with a `q5_0` KV cache; bare `qwen3.5:9b` works too as a fallback)
- API key (free tier) for **Adzuna CA**: <https://developer.adzuna.com/>

## Install

```bash
git clone <this-repo>
cd jobhunt
uv sync
source .venv/bin/activate        # activates the jobhunt venv; `jobhunt` is now on PATH
playwright install chromium

ollama pull qwen3.5:9b           # base model — score, tailor, cover
ollama pull nomic-embed-text    # embeddings (reserved for future use)
```

### Ollama systemd settings

The gateway is tuned to a specific server configuration. Mirror these
(Arch / systemd: `sudo systemctl edit ollama.service`):

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q5_0"      # q5_0 KV cache — ~30% VRAM saving
Environment="OLLAMA_FLASH_ATTENTION=1"       # required for a quantized KV cache
Environment="OLLAMA_NUM_PARALLEL=1"          # one concurrent request
Environment="OLLAMA_CONTEXT_LENGTH=16384"    # 16k context window
Environment="OLLAMA_KEEP_ALIVE=-1"           # never unload the hot model
Environment="OLLAMA_MAX_LOADED_MODELS=1"     # one model in VRAM at a time
```

The gateway sends matching per-call values (`num_ctx=16384`, `keep_alive=-1`)
so behavior stays consistent regardless of which side has the policy. If
you change one side, change the other.

### Model variant

Default model in config is `qwen-custom:latest` — a Modelfile-derived
`qwen3.5:9b` that bakes in personal prompt context (see your AI context stack
build script). The gateway always sends a system message, which overrides the
Modelfile SYSTEM for structured tasks, so the persona doesn't bleed into
tailoring/scoring output.

If you haven't built `qwen-custom`, point all three slots back at `qwen3.5:9b`
in `~/.config/jobhunt/config.toml` under `[gateway.tasks]`. Same VRAM
footprint and quirks either way — they share base weights.

After `uv sync`, the `jobhunt` script is installed into `.venv/bin`. Activate the
venv once per shell (`source .venv/bin/activate`) and use `jobhunt` directly. If
you'd rather not activate, prefix any command with `uv run` (e.g. `uv run jobhunt scan`).

## Commands

Four user-facing commands.

```bash
jobhunt convert-resume       # parse Resume.docx → kb/profile/
jobhunt scan                 # ingest GTA jobs + score against profile
jobhunt apply <job-id>       # tailor + cover + autofill (you submit)
jobhunt apply --top N        # auto-pick N best-fit unapplied jobs (1..10)
jobhunt apply --best         # interactive pick from top 10
jobhunt apply --url <URL>    # ad-hoc: fetch JD from URL, score, tailor (you submit)
jobhunt list [--week N]      # pipeline view + weekly tracking
```

`db` and `config` exist as hidden internal commands for setup; they don't
appear in `--help`.

### `apply` selection modes

- `apply <job-id>` — single job by id
- `apply --top N` — N highest-scoring unapplied jobs above `--min-score`
  (default 65). Capped at 10.
- `apply --best` — lists the top 10 candidates and prompts for picks like
  `1,3,7` or `2-5`.

Add `--no-browser` to generate the tailored docs without launching Playwright.

### Manual applying (one-off URL)

When a posting isn't in the scan results — a friend's tip, a direct careers
page, a LinkedIn link — bypass `scan` and feed the URL straight in:

```bash
jobhunt apply --url "https://boards.greenhouse.io/<employer>/jobs/<id>"
```

The fetcher renders the page in headless Chromium via Playwright (so
JS-heavy career portals like Workday, Phenom, iCIMS, SuccessFactors actually
load their JD content), extracts title/company/body from the rendered HTML,
persists as `source=manual`, scores it, then runs the normal
tailor/cover/audit pipeline.

Auto-detection of title/company from the URL's `<title>` / OG tags is
best-effort. If it fails, pass `--title` and `--company` explicitly. Both
override auto-detection when used with `--url`.

Escape hatches:
- `--no-score` — skip the ~30–60 s scoring pass. Audit's keyword-coverage falls
  back to title/JD-only must-haves.
- `--force-robots` — fetch a URL even when robots.txt disallows. Personal-use
  single-shot only; does not relax the rule for `scan` adapters.

Bump status after submitting (or to mark interview / offer / rejected):

```bash
jobhunt apply --set-status applied      <job-id>
jobhunt apply --set-status interviewing <job-id>
jobhunt apply --set-status rejected     <job-id>
```

The flag must come **before** the job id.

### `list` filters

- `--week N` — 0=current week, 1=last week, …
- `--status drafted|applied|interviewing|offer|rejected`
- `--min-score N`, `--source greenhouse|lever|ashby|smartrecruiters|workday|job_bank_ca|rss|adzuna_ca`
- Always renders a weekly rollup footer (scanned / declined / per-status counts).

## First run

> Note: `config` and `db` are setup-only commands — they're hidden from `--help` after install. Run them once during setup as shown below.

```bash
jobhunt config show       # writes a default config and prints it
jobhunt db init           # creates SQLite schema at data/jobhunt.db
jobhunt convert-resume    # generates kb/profile/* from the baseline
```

`scan`, `list`, and `apply` will refuse to run until `convert-resume` has been
executed at least once — they need `kb/profile/verified.json` as the source of
truth.

To start over from scratch (drops the DB, all tailored documents, the HTTP
cache, the browser profile, and the parsed resume):

```bash
jobhunt db reset          # prompts for 'yes', then re-inits schema
jobhunt convert-resume    # regenerate kb/profile/ before scanning
```

### Configure ingest sources

Adzuna CA covers a broad slice of public postings, but for direct ATS feeds
you need to add per-employer slugs. Edit `~/.config/jobhunt/config.toml`:

```toml
[ingest]
greenhouse      = ["shopify", "1password", "wealthsimple", "faire"]
lever           = ["benchsci", "ada"]
ashby           = ["cohere"]
smartrecruiters = []   # company slugs, e.g. "Bosch", "Visa"
workday         = []   # "tenant:host:site" triples, see ingest/workday.py
job_bank_ca     = []   # full RSS URLs from jobbank.gc.ca search results
rss             = []   # generic employer career-page RSS/Atom URLs

[ingest.adzuna]
# Leave `queries = []` (or omit entirely) to auto-derive from
# `kb/profile/verified.json` — the planner walks skills_core / skills_cms /
# work bullets and emits up to 10 role-suffixed queries including umbrella
# signals ("cms developer", "ai engineer", "seo specialist"). Populate to
# override with a verbatim list, e.g.:
#   queries = ["javascript developer", "react developer", "ai engineer"]
queries = []

[applicant]
phone = "(416) 555-0123"
salary_expectation_cad = "50k–90k"
```

**Finding slugs:**
- **Greenhouse**: visit `<company>.com/careers` — if it redirects to or
  embeds `boards.greenhouse.io/<slug>`, that's the slug.
- **Lever**: same idea — look for `jobs.lever.co/<slug>`.
- **Ashby**: look for `jobs.ashbyhq.com/<slug>`.
- **SmartRecruiters**: `careers.smartrecruiters.com/<Company>` — slug is
  the path segment after the host.
- **Workday**: harder. Open the company careers page, copy the
  `tenant:host:site` triple from the Workday URL (see comments in
  `src/jobhunt/ingest/workday.py`).

If only `adzuna_ca` is configured, `scan` prints a warning — single-source
scans are biased and miss large GTA employers (Shopify, RBC, etc.) that
publish only via their own ATS.

API keys live in `~/.config/jobhunt/secrets.toml` (chmod 0600) or env vars:

```toml
adzuna_app_id  = "..."
adzuna_app_key = "..."
```

## Daily flow

```bash
jobhunt scan                      # pulls new jobs + scores them
jobhunt list --min-score 70       # see today's high-fit subset
jobhunt apply --best              # pick which to apply to
# Browser opens. You review, click Submit yourself.
jobhunt list --week 0             # weekly pipeline view
```

## Data layout

| Path | What lives there |
|---|---|
| `Resume.docx` | Source-of-truth resume. Hand-edited. |
| `Resume_Tailoring_Instructions.md` | Hard rules (no fabrication, ATS-safe, auto-decline). |
| `kb/profile/verified.json` | Structured facts emitted by `convert-resume`. Tailoring is constrained to this. |
| `kb/policies/tailoring-rules.md` | Prompt-injectable mirror of the Tailoring Instructions. |
| `kb/prompts/{score,tailor,cover}.md` | Prompts with JSON-schema frontmatter. |
| `~/.config/jobhunt/config.toml` | Sources, models, applicant profile, paths. |
| `~/.config/jobhunt/secrets.toml` | API keys (Adzuna), mode 0600. |
| `data/jobhunt.db` | SQLite — jobs, scores, applications. |
| `data/applications/<job-id>/` | Tailored resume, cover letter, fill-plan.json. |

`data/` is gitignored.

## Honesty signals enforced in code

- Tailoring output rejects any role whose `(employer, dates)` is missing from
  `verified.json`.
- Skill items must match `verified.json` (substring tolerance for parenthetical
  variants).
- "Familiar" skills cannot appear in any non-Familiar category.
- Score prompt sets `decline_reason` for: 4+ hard gaps after transferable
  matching, years explicitly required > 5 with no project bridge,
  Lead/Principal/Architect/Staff title with stated team-leadership scope,
  people-management title, non-engineering function, regulated-domain
  experience required, or location ineligible. Bare "Senior" alone is
  **not** a decline trigger. Declined jobs are excluded from
  `apply --top` / `apply --best`.
- Post-generation audit (`pipeline.audit`) checks JD must-have keyword
  coverage against the rendered resume at ≥70%, re-runs the no-fabrication
  enforcement, and runs the cover-letter validator. If `scores.reasons` is
  empty (qwen sometimes ships empty arrays despite the schema), the audit
  falls back to deterministic must-have extraction by intersecting verified
  skills with **both** `job.title` and `job.description` — title is included
  because some sources (e.g. Adzuna) ship truncated 500-char descriptions
  where canonical tech names like "Java" or "React" only appear in the title.
- Cover-letter validator normalizes curly/smart apostrophes (`'` → `'`)
  before banned-phrase matching, so qwen's typographic output can't sneak
  phrases like "team's goals" past the substring check.
- Clock-style time references in the cover body (`11:00 AM`, `9 a.m.`, `5pm`,
  bare `12:30`) are stripped before the unverified-numbers pass — a JD
  stand-up reference shouldn't be flagged as fabrication.
- One-page guarantee is enforced by `pipeline.tailor._shrink_to_one_page`:
  trim summary to ≥3 sentences → trim Familiar to ≥4 items → drop the last
  bullet of the heaviest role (preserves each role's JD-relevant lead) → drop
  the coursework block. If the resume still overflows, the tailor raises.

See [Resume_Tailoring_Instructions.md](Resume_Tailoring_Instructions.md) for
the full rule set; [kb/policies/tailoring-rules.md](kb/policies/tailoring-rules.md)
is the prompt-injectable mirror.

## License

Personal-use project. No license granted for redistribution at this time.
