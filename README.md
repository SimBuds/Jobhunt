# job-seeker

A local-first CLI for Casey's Toronto-area job hunt. Pulls jobs from public ATS
APIs (Greenhouse, Lever, Ashby, Adzuna CA) scoped to GTA + 100 km and
Remote-Canada postings, fit-scores them against the parsed baseline resume
using local Ollama models, drafts a tailored resume and cover letter per role,
and assists with form autofill in the browser. **You submit every application
yourself.** The tool fills the form; it never clicks Submit.

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
- ~10 GB free VRAM for the 14B-model pipeline; 6 GB works on the 8B-only path
- API key (free tier) for **Adzuna CA**: <https://developer.adzuna.com/>

## Install

```bash
git clone <this-repo>
cd Caseys-Job-Seeker
uv sync
uv run playwright install chromium

ollama pull qwen3:14b           # tailoring, cover letters
ollama pull qwen3:8b            # scoring, classification
ollama pull nomic-embed-text    # embeddings (reserved for future use)
```

## Commands

Four user-facing commands.

```bash
job-seeker convert-resume       # parse Casey_Hsu_Resume_Baseline.docx → kb/profile/
job-seeker scan                 # ingest GTA jobs + score against profile
job-seeker apply <job-id>       # tailor + cover + autofill (you submit)
job-seeker apply --top N        # auto-pick N best-fit unapplied jobs (1..10)
job-seeker apply --best         # interactive pick from top 10
job-seeker list [--week N]      # pipeline view + weekly tracking
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
job-seeker apply --set-status applied      <job-id>
job-seeker apply --set-status interviewing <job-id>
job-seeker apply --set-status rejected     <job-id>
```

The flag must come **before** the job id.

### `list` filters

- `--week N` — 0=current week, 1=last week, …
- `--status drafted|applied|interviewing|offer|rejected`
- `--min-score N`, `--source greenhouse|lever|ashby|adzuna_ca`
- Always renders a weekly rollup footer (scanned / declined / per-status counts).

## First run

```bash
uv run job-seeker config show       # writes a default config and prints it
uv run job-seeker db init           # creates SQLite schema at data/jobhunt.db
uv run job-seeker convert-resume    # generates kb/profile/* from the baseline
```

Edit `~/.config/jobhunt/config.toml` to add company slugs:

```toml
[ingest]
greenhouse = ["stripe", "shopify", "1password"]
lever      = ["benchsci", "ada"]
ashby      = ["ramp", "linear"]

[applicant]
phone = "(416) 555-0123"
salary_expectation_cad = "100k–120k"
```

API keys live in `~/.config/jobhunt/secrets.toml` (chmod 0600) or env vars:

```toml
adzuna_app_id  = "..."
adzuna_app_key = "..."
```

## Daily flow

```bash
uv run job-seeker scan                      # pulls new jobs + scores them
uv run job-seeker list --min-score 70       # see today's high-fit subset
uv run job-seeker apply --best              # pick which to apply to
# Browser opens. You review, click Submit yourself.
uv run job-seeker list --week 0             # weekly pipeline view
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
- Score prompt sets `decline_reason` when 3+ JD must-haves are gaps, years gap
  > 2x, senior title, regulated domain, or location ineligible. Declined jobs
  are excluded from `apply --top` / `apply --best`.

See [Resume_Tailoring_Instructions.md](Resume_Tailoring_Instructions.md) for
the full rule set; [kb/policies/tailoring-rules.md](kb/policies/tailoring-rules.md)
is the prompt-injectable mirror.

## License

Personal-use project. No license granted for redistribution at this time.
