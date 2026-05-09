# Jobhunt

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
- ~10 GB VRAM (qwen3.5:9b lands at ~9 GB resident at `num_ctx=6144`)
- API key (free tier) for **Adzuna CA**: <https://developer.adzuna.com/>

## Install

```bash
git clone <this-repo>
cd jobhunt
uv sync
uv run playwright install chromium

ollama pull qwen3.5:9b           # single hot model — score, tailor, cover
ollama pull nomic-embed-text    # embeddings (reserved for future use)
```

## Commands

Four user-facing commands.

```bash
jobhunt convert-resume       # parse Casey_Hsu_Resume_Baseline.docx → kb/profile/
jobhunt scan                 # ingest GTA jobs + score against profile
jobhunt apply <job-id>       # tailor + cover + autofill (you submit)
jobhunt apply --top N        # auto-pick N best-fit unapplied jobs (1..10)
jobhunt apply --best         # interactive pick from top 10
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
uv run jobhunt config show       # writes a default config and prints it
uv run jobhunt db init           # creates SQLite schema at data/jobhunt.db
uv run jobhunt convert-resume    # generates kb/profile/* from the baseline
```

`scan`, `list`, and `apply` will refuse to run until `convert-resume` has been
executed at least once — they need `kb/profile/verified.json` as the source of
truth.

To start over from scratch (drops the DB, all tailored documents, the HTTP
cache, the browser profile, and the parsed resume):

```bash
uv run jobhunt db reset          # prompts for 'yes', then re-inits schema
uv run jobhunt convert-resume    # regenerate kb/profile/ before scanning
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
queries = ["javascript developer", "react developer", "shopify developer"]

[applicant]
phone = "(416) 555-0123"
salary_expectation_cad = "100k–120k"
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
uv run jobhunt scan                      # pulls new jobs + scores them
uv run jobhunt list --min-score 70       # see today's high-fit subset
uv run jobhunt apply --best              # pick which to apply to
# Browser opens. You review, click Submit yourself.
uv run jobhunt list --week 0             # weekly pipeline view
```

## Data layout

| Path | What lives there |
|---|---|
| `Casey_Hsu_Resume_Baseline.docx` | Source-of-truth resume. Hand-edited. |
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
  skills with the JD description.

See [Resume_Tailoring_Instructions.md](Resume_Tailoring_Instructions.md) for
the full rule set; [kb/policies/tailoring-rules.md](kb/policies/tailoring-rules.md)
is the prompt-injectable mirror.

## License

Personal-use project. No license granted for redistribution at this time.
