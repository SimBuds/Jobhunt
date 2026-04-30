# job-seeker — Design notes

A CLI tool that ingests Toronto-area jobs from public ATS APIs, scores them
against Casey's parsed baseline resume using local Ollama models, tailors
resumes and cover letters per role, and assists with form autofill in a
headed browser. Casey clicks Submit.

This document explains the *why*. `CLAUDE.md` is the *how* (conventions,
guardrails, project structure). `README.md` is for end-users.

---

## Goals

1. **Replace cloud per-job AI spend with local inference.** Scoring,
   tailoring, and cover letters all run on Ollama. Token cost at runtime is
   zero.
2. **Future-proof the knowledge layer.** Profile facts live in
   `kb/profile/verified.json` (regenerated from `Casey_Hsu_Resume_Baseline.docx`).
   Prompts live in `kb/prompts/*.md` with JSON-schema frontmatter. Model swaps
   don't break them.
3. **Stay ToS-defensible.** Public ATS APIs only. No LinkedIn / Indeed /
   Glassdoor scraping. No bot-submitted applications — Playwright fills the
   form, human clicks Submit.
4. **Honesty by construction.** Tailoring is constrained to facts in
   `verified.json`. Roles must match `(employer, dates)` exactly. "Familiar"
   skills can't be promoted into Core categories. The score prompt
   auto-declines roles >2x Casey's experience or with senior titles.

## Design principles

**Local-first at runtime.** Every per-job AI call routes through
`jobhunt.gateway` to Ollama at `http://localhost:11434`. No cloud calls in
the hot path.

**Constrained output.** Every structured LLM call uses Ollama's `format`
parameter with a JSON schema from the prompt's frontmatter.

**Cascade by difficulty.** Cheap tasks (fit-score, classification) → 8B
model. Generation tasks (tailored resume, cover letter) → 14B model. Set in
config (`gateway.tasks`).

**Knowledge base is markdown + JSON.** No model-specific syntax baked in.

## Hardware budget

| Resource | Allocation |
|---|---|
| GPU VRAM (10 GB) | One hot model at a time. Default: Qwen3 14B Q4_K_M (~9.5 GB at 8K ctx). Swap to 8B Q5_K_M (~6 GB) for speed. |
| System RAM (32 GB) | Embeddings on CPU; SQLite cache; Playwright when active. |
| Disk | Models in `~/.ollama/models`; project DB in `data/jobhunt.db`. |

## Models (default)

| Task | Model | Why |
|---|---|---|
| Fit-score (JSON) | `qwen3:8b` | Fast, schema-constrained. |
| Tailor resume | `qwen3:14b` | Better instruction-following + larger context. |
| Cover letter | `qwen3:14b` | Voice/style matters. |
| Embeddings | `nomic-embed-text` | CPU. Reserved for future kb retrieval. |

All overridable in `~/.config/jobhunt/config.toml`.

## Sources (Toronto-focused)

- **Greenhouse** boards-api — most common in GTA tech listings.
- **Lever** `api.lever.co/v0/postings/<slug>` — common at GTA startups.
- **Ashby** posting API — growing share in 2025–26.
- **Adzuna CA** — `country=ca&where=Toronto&distance=100`. Aggregates broadly;
  needs a free API key.

Filter pipeline: each adapter checks `is_gta_eligible(location)` before
yielding a job. The allowlist covers Toronto + 13 surrounding municipalities,
plus Remote-Canada / Remote-Ontario / Remote-EST. Bare "Remote" is rejected
as ambiguous.

Explicitly excluded (won't change without removing the no-scraping guardrail
in `CLAUDE.md`):

- LinkedIn, Indeed, Glassdoor, ZipRecruiter — ToS, brittle, litigated.
- USAJobs and worldwide job APIs — out of GTA scope.

## What this project deliberately doesn't do

- Auto-submission of applications (ToS risk; human stays in the loop).
- Web UI / mobile (CLI-first; no current need).
- Recruiter outreach automation (different problem; do not bolt on).

## Database

SQLite, plain SQL, no ORM. Schema in `migrations/`:

- `0001_init.sql` — companies, jobs, scores, applications, indexes.
- `0002_apply_tracking.sql` — adds `jobs.decline_reason` and
  `applications.applied_week` (ISO week label, e.g. "2026-W18") for cheap
  weekly rollups.

## Honesty enforcement (the structural part)

The "no fabrication" rule from `Resume_Tailoring_Instructions.md` is enforced
in three places, not just the prompt:

1. **Verified snapshot.** `convert-resume` emits `kb/profile/verified.json`.
   The tailoring prompt is constrained to only use facts from this file.
2. **Schema-constrained output.** `kb/prompts/tailor.md` declares a JSON
   schema. Ollama's `format=<schema>` enforces shape at decode time.
3. **Post-decode invariants.** `pipeline.tailor._enforce_no_fabrication`:
   - rejects any role whose `(employer, dates)` is missing from
     `verified.json`;
   - rejects skill items not present in `verified.json` (substring tolerance
     for parenthetical variants like `Shopify (Liquid)` vs
     `Shopify (Liquid, Custom Themes)`);
   - rejects "Familiar" skills appearing in any non-Familiar category.

If any check fails, the apply pipeline aborts for that job rather than
producing a misleading resume.

## Success criteria

- Pulls fresh GTA jobs daily across configured Greenhouse/Lever/Ashby/Adzuna
  sources without ToS issues.
- Scores every new job within minutes of ingestion.
- Generates a tailored .docx + cover letter in <90 seconds on local hardware.
- Autofills standard fields on Greenhouse forms; falls back to a generic
  selector-based handler elsewhere.
- Zero cloud API spend at runtime.
