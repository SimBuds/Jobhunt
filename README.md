# job-seeker

A local-first CLI for Casey's Toronto-area job hunt. Pulls jobs from public ATS
APIs scoped to GTA + 100 km (and Remote-Canada eligible postings), fit-scores
them against the parsed baseline resume using local LLMs, drafts tailored
resumes and cover letters per role, and assists with form autofill in the
browser. **You submit every application yourself.** The tool fills the form;
it never clicks Submit.

Everything runs locally. No resume or job data leaves your hardware. Zero cloud
LLM calls in the runtime path.

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

ollama pull qwen3:14b           # tailoring, cover letters
ollama pull qwen3:8b            # scoring, classification
ollama pull nomic-embed-text    # embeddings
```

## Commands

The user-facing surface is four commands.

```bash
job-seeker convert-resume       # parse Casey_Hsu_Resume_Baseline.docx → kb/profile/
job-seeker scan                 # ingest GTA jobs + score against profile     [P2]
job-seeker apply <job-id>       # tailor resume+cover letter, autofill form   [P3+P4]
job-seeker list [--week N]      # pipeline view + weekly tracking             [P5]
```

`db` and `config` exist as hidden internal commands for setup; they don't
appear in `--help`.

## First run

```bash
uv run job-seeker config show       # writes a default config and prints it
uv run job-seeker db init           # creates SQLite schema at data/jobhunt.db
uv run job-seeker convert-resume    # generates kb/profile/* from the baseline .docx
```

The default config lives at `~/.config/jobhunt/config.toml`. Override any value
with a `JOBHUNT_` env var (nested keys use `__`):

```bash
JOBHUNT_GATEWAY__BASE_URL=http://other-host:11434/v1 uv run job-seeker config show
```

API keys live in `~/.config/jobhunt/secrets.toml` (chmod 0600) or as env vars.

## Data layout

| Path | What lives there |
|---|---|
| `Casey_Hsu_Resume_Baseline.docx` | Source-of-truth resume. Hand-edited. |
| `Resume_Tailoring_Instructions.md` | Hard rules for tailoring (no-fabrication, ATS-safe, auto-decline). |
| `kb/profile/verified.json` | Structured facts emitted by `convert-resume`. Tailoring is constrained to this. |
| `kb/policies/tailoring-rules.md` | Prompt-injectable mirror of the Tailoring Instructions. |
| `~/.config/jobhunt/config.toml` | Sources, models, paths. |
| `~/.config/jobhunt/secrets.toml` | API keys, mode 0600. |
| `data/jobhunt.db` | SQLite — jobs, scores, applications. |
| `data/applications/<job-id>/` | Tailored resume, cover letter, fill plan. |

`data/` is gitignored.

## Build status

| Phase | What | Status |
|---|---|---|
| P1 | `convert-resume` + KB regen + tailoring rules mirror | ✅ done |
| P2 | `scan` (GTA ingest + Ollama scoring) | pending |
| P3 | `apply` part 1 (tailoring + ATS-safe .docx render) | pending |
| P4 | `apply` part 2 (Playwright autofill: Greenhouse/Lever/Ashby/Workday + generic fallback) | pending |
| P5 | `list` + weekly tracking + migration `0002_weekly_tracking.sql` | pending |

`PLAN.md` is the strategic roadmap. `CLAUDE.md` has the conventions Claude Code
auto-loads. The active design doc for this refactor is
`/home/casey/.claude/plans/i-think-the-last-gentle-key.md`.
