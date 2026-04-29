# jobhunt — Implementation Plan

A CLI tool that ingests jobs from ATS APIs, scores them against your profile using local LLMs, tailors resumes and cover letters per role, and assists with form autofill. Runs on your Arch + Ryzen 5900 + 32GB + RTX 3080 setup. Built with Claude Code, run on Ollama.

---

## Goals

1. **Replace cloud per-job AI spend with local inference.** Scoring, tailoring, and Q&A all run on Ollama. Token cost at runtime is zero.
2. **Use Claude Code as the build environment**, not the runtime. Architecture decisions, refactors, and hard debugging go to Claude Code via `AGENTS.md` prompts.
3. **Future-proof the knowledge layer.** Profile, prompts, schemas, examples, and conventions live in `kb/` as plain markdown and JSON. Model swaps don't break them.
4. **Stay ToS-defensible.** Public ATS APIs only. No LinkedIn/Indeed scraping. No bot-submitted applications — Playwright fills the form, human clicks Submit.

---

## Design principles

**Local-first at runtime.** Every per-job AI call routes through a local OpenAI-compatible gateway to Ollama. No cloud calls in the hot path.

**Constrained output.** Any LLM call producing structured data uses a JSON schema (via Ollama's `format` parameter or llama.cpp grammar). A 7B with a schema beats a 70B without one.

**Cascade by difficulty.** Cheap tasks (fit-score, classification, JSON extraction) → 8B model. Generation tasks (cover letter, tailored summary) → 14B model. Reasoning-heavy (debugging a weird application form) → optionally Claude Code via API.

**Eval-gated changes.** Prompt and model changes must pass a local eval suite before they merge. Without this, "I tweaked the prompt and it feels better" is how you regress silently.

**Knowledge base is markdown.** No model-specific syntax baked in. Gateway translates to whatever runtime expects.

---

## Hardware budget

| Resource | Allocation |
|---|---|
| GPU VRAM (10GB) | One hot model at a time. Default: Qwen3 14B Q4_K_M (~9.5GB at 8K ctx). Swap to Qwen3 8B Q5_K_M (~6GB) for speed-bound tasks. |
| System RAM (32GB) | Embeddings model in CPU; SQLite cache; Playwright when active |
| Disk | Models in `~/.ollama/models`; project DB in `data/jobhunt.db` |

Keep `num_ctx` honest. KV cache at 8K on a 14B is ~2GB. Don't blow the VRAM budget on context you won't use.

---

## Repo layout (target end-state)

```
jobhunt/
├── CLAUDE.md                  # project context for Claude Code (auto-loaded)
├── PLAN.md                    # this file
├── AGENTS.md                  # phase-by-phase implementation prompts
├── README.md
├── pyproject.toml             # uv-managed
├── .env.example
├── src/jobhunt/
│   ├── cli.py                 # Typer entry point
│   ├── config.py              # TOML config loading
│   ├── db.py                  # SQLite + migrations
│   ├── ingest/                # ATS adapters (one file per source)
│   ├── gateway/               # Ollama client + model router + prompt loader
│   ├── pipeline/              # score, tailor, cover, qa
│   ├── browser/               # Playwright autofill (human submits)
│   └── kb_loader.py           # reads kb/ at runtime
├── kb/                        # KNOWLEDGE BASE — git-tracked, model-portable
│   ├── profile/
│   │   ├── resume.md
│   │   ├── work-history.md
│   │   ├── skills.md
│   │   ├── stories.md         # STAR-format examples
│   │   └── voice.md           # writing samples for tone matching
│   ├── prompts/
│   │   ├── score.md
│   │   ├── tailor.md
│   │   ├── cover.md
│   │   └── qa.md
│   ├── schemas/               # JSON schemas for constrained output
│   ├── stacks/                # tech conventions (python-uv.md, etc.)
│   └── examples/              # few-shot good/bad pairs
├── data/                      # SQLite DB, gitignored
├── evals/                     # regression suite for prompts
└── tests/
```

---

## Phased roadmap

Each phase is a discrete unit Claude Code can implement in one session. Exit criteria are testable. Don't start phase N+1 until N's exit criteria pass.

### Phase 0 — Foundations (½ day)

Bootstrap the repo, dependency management, SQLite schema, config loading, and `kb/` skeleton with a starter `profile/resume.md` you can fill in.

**Exit:** `jobhunt --help` runs. `jobhunt db init` creates the schema. `kb/profile/resume.md` exists. Tests pass.

### Phase 1 — Ingest (1–2 days)

ATS adapters for Greenhouse, Lever, Ashby. USAJobs API client. Adzuna API client. RSS catch-all. Dedup by `(source, external_id)`. Persist to SQLite.

**Exit:** `jobhunt ingest run` populates `data/jobhunt.db` with ≥100 jobs across all three source categories. `jobhunt list` shows them. Rate-limited, polite User-Agent, robots.txt respected.

### Phase 2 — Gateway (½–1 day)

OpenAI-compatible client pointing at `http://localhost:11434/v1`. Model router that maps task names (`score`, `tailor`, `cover`) to model tags via config. Prompt loader that composes from `kb/`. Eval harness scaffold.

**Exit:** `jobhunt model list` shows installed Ollama models. `jobhunt model test score` runs a fixed prompt and reports tokens/sec + output. `jobhunt eval run` executes ≥3 fixture cases.

### Phase 3 — Pipeline: scoring (½–1 day)

Fit-score every unscored job against `kb/profile/`. Constrained JSON output: `{score: 0-100, reasons: [str], red_flags: [str], must_clarify: [str]}`. Stored on the job row.

**Exit:** `jobhunt score --unscored` processes the backlog. `jobhunt list --min-score 70` returns the high-fit subset. Eval suite covers ≥5 representative jobs with expected score bands.

### Phase 4 — Pipeline: tailoring (1 day)

For a given job ID, generate (a) a tailored resume markdown by re-ordering and rephrasing from `work-history.md` + `stories.md`, and (b) a cover letter draft in your voice from `voice.md`. Both saved to `data/applications/<job-id>/`.

**Exit:** `jobhunt tailor <id>` produces `resume.md` and `cover-letter.md`. Eval suite checks for: ≥3 keywords from job description present, no fabricated employers/dates, length within bounds.

### Phase 5 — CLI review TUI (½ day)

Interactive review loop: show next high-score job, display fit reasoning, accept `[a]pply / [s]kip / [t]ailor / [q]uit`. Built on `prompt_toolkit` or `textual`.

**Exit:** `jobhunt review` is the primary daily-use command. Feels fast.

### Phase 6 — Autofill (1–2 days)

Playwright-based form fill for the major ATS form patterns (Greenhouse, Lever, Ashby, Workday). Reads tailored docs from disk, fills fields, leaves browser open for human review and submit. Logs the field-fill plan to disk for auditability.

**Exit:** `jobhunt apply <id>` opens browser, fills 80%+ of fields correctly on the three ATS targets, never clicks submit.

### Phase 7 — Tracking + insights (½ day)

Status transitions: `discovered → scored → tailored → applied → interviewing → offer/rejected`. `jobhunt status <id> --set <status>`. Weekly digest: which sources convert, which prompts produce the highest-scoring outputs.

**Exit:** A 30-day rolling view exists. You can answer "of the jobs I scored ≥80, what % did I actually apply to" with one command.

---

## Model selection (default)

| Task | Model | Quant | Why |
|---|---|---|---|
| Fit-score (JSON) | `qwen3:8b` | Q5_K_M | Fast, schema-constrained, reasoning sufficient |
| Tailor resume | `qwen3:14b` | Q4_K_M | Better instruction following, larger context |
| Cover letter | `qwen3:14b` | Q4_K_M | Voice/style sensitivity matters |
| Form Q&A | `qwen3:8b` | Q5_K_M | Short answers, retrieval-driven |
| Embeddings | `nomic-embed-text` | — | CPU, used for kb retrieval |

All overridable in `config.toml`. The eval suite is what tells you when to swap.

---

## Success criteria for v1

- Pulls ≥500 fresh jobs/day across configured sources without ToS issues
- Scores every new job within 5 minutes of ingestion
- Generates a tailored resume + cover letter in <90 seconds on local hardware
- Autofills 80%+ of fields correctly on Greenhouse/Lever/Ashby
- Eval suite catches prompt regressions before they hit production
- Zero cloud API spend at runtime

---

## What this plan deliberately excludes

- Auto-submission of applications (ToS risk; human stays in the loop)
- LinkedIn / Indeed scraping (ToS, brittle, litigated)
- A web UI (CLI-first per your call; can add later)
- Mobile (out of scope)
- Recruiter outreach automation (different problem; do not bolt on)
