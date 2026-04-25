# agent-instructions.md

Guide for human + AI agents working on `job-agent`. Read this before making non-trivial changes.

## What this project is

An AI-assisted personal job search operating system. Local-first, human-in-the-loop. Runs on the user's machine against local Ollama models. Helps discover, rank, tailor, and apply — the user always clicks the final submit button.

Portfolio framing: *"AI-Assisted Personal Job Search Operating System"* — never *"AI auto-applies to jobs"*.

## Non-negotiable rules

### MUST

- require manual final review and manual submit for every application
- preserve truthfulness in tailoring (rewording + reordering existing bullets only)
- maintain application logs (SQLite) and feedback log (`feedback.md`)
- prioritize Toronto / GTA / Hybrid Toronto / Remote Canada developer roles
- keep local-only — no telemetry, no external LLM calls, no cloud storage
- stay clean enough for public portfolio demonstration
- print clear, actionable errors (path, reason, what to do next)

### MUST NOT

- auto-submit applications
- bypass CAPTCHA, logins, or other access controls
- fabricate experience, metrics, or projects in resume/cover letter output
- spam mass applications (apply-all is capped at 10 and requires per-job confirm)
- swallow errors silently — surface them with useful context
- add external API dependencies without explicit user sign-off

## Current architecture

### Commands ([cli.js](cli.js))

| Command | Purpose |
|---|---|
| `convert [file]` | `.pdf`/`.docx`/`.txt` → `base-resume.json`. Auto-detects resume files at project root. |
| `scan` | Discover + score jobs into `applications/pipeline.json`. `--sources api,linkedin,jobbank,all`. All searches are centered on Toronto + 100km across every work type. |
| `apply` | Menu of top-10 unapplied jobs. Pick one / apply-all / cancel. `--url <url>` for direct. |
| `report` | Weekly CLI summary. |
| `list` | Tracked applications from SQLite. |
| `status <id> <new>` | Update an application's status. |

Every command has an `npm run` alias in `package.json`.

### Module map ([src/](src/))

**`src/core/`** — cross-cutting infrastructure
- [config.js](src/core/config.js) — `data/config.json` loader (seniority policy, pipeline cap, verbose flag)
- [companies.js](src/core/companies.js) — `data/companies.json` loader
- [track.js](src/core/track.js) — SQLite, dedupe by URL
- [feedback.js](src/core/feedback.js) — append-only `feedback.md`
- [prompt.js](src/core/prompt.js) — `ask` / `askYesNo` readline wrappers
- [stream.js](src/core/stream.js) — Ollama stream watchdog (45s stall, 5min max) + retry

**`src/apply/`** — scrape → analyze → tailor → render → autofill pipeline
- [scrape.js](src/apply/scrape.js) — platform-aware JD extraction (cheerio + 20s timeout + 24h cache)
- [analyze.js](src/apply/analyze.js) — `qwen2.5-coder:7b` → structured JSON (requirements/keywords/company/role/tone). Re-prompts once if role_title/company_name is unclear before throwing.
- [tailor.js](src/apply/tailor.js) — `gemma4:e2b` rewrites summary + reorders bullets. Owns `base-resume.json` loading. Validates every output bullet via Jaccard similarity (≥0.6) against source — rejects hallucinations.
- [coverletter.js](src/apply/coverletter.js) — ~250-word draft using `qwen2.5-coder:7b`. Validates role_title present in JD. Scans output for 28 cliché phrases and regenerates once if found.
- [render.js](src/apply/render.js) — pdf-lib → PDFs in `output/`
- [autofill.js](src/apply/autofill.js) — Playwright headful, fills Greenhouse/Lever/Ashby/SmartRecruiters/Workday

**`src/discover/`** — scan + scoring + job sources
- [scan.js](src/discover/scan.js) — Greenhouse + Lever APIs, normalization, fuzzy dedup, stale-marking, seniority policy application. ROLE_DENY_RE blocks off-target titles (DevOps, QA, PM, etc.); `role_deny_extras` in config extends it.
- [score.js](src/discover/score.js) — deterministic fit score (keyword/stack/title/education/ATS factors). No LLM.
- [sources/browser-search.js](src/discover/sources/browser-search.js) — stealth Playwright launcher (persistent context, UA spoof, webdriver removal, jitter, 429 backoff, login/CAPTCHA detection)
- [sources/linkedin.js](src/discover/sources/linkedin.js) — LinkedIn guest-search parser (100km radius, all work types)
- [sources/jobbank.js](src/discover/sources/jobbank.js) — Government of Canada Job Bank parser (public, no ToS issue)

**`src/` (standalone commands)**
- [convert.js](src/convert.js) — PDF/DOCX → base-resume.json via LLM (with hyperlink extraction + backfill)
- [report.js](src/report.js) — CLI dashboard

### Data files

- `base-resume.json` — source of truth, project root. Never invented by code.
- `data/companies.json` — Greenhouse/Lever slugs + per-scraper query lists
- `data/config.json` — tunable behavior (seniority policy, pipeline cap, verbose scan)
- `data/applications.db` — SQLite history
- `data/browser-profile/` — persistent Chromium profile for LinkedIn cookies (gitignored)
- `applications/pipeline.json` — scored discovery output
- `feedback.md` — post-apply notes (append-only)
- `output/*.pdf` — rendered resumes + cover letters

### Models

| Model | Role |
|---|---|
| `qwen2.5-coder:7b` | JSON extraction + cover letter (analyze, convert, coverletter) |
| `gemma4:e2b` | Resume tailoring (tailor) |
| `qwen3.5:4b` | Fast fallback with `--fast` (tailor, coverletter) |

All calls stream with watchdog. Ollama at `http://127.0.0.1:11434`.

## QA checklist

Run before shipping changes. Each item is a smoke test — not exhaustive.

### Setup sanity

- [ ] `node cli.js --help` — all six commands listed
- [ ] `npm run scan --help` (or `-- --help`) — shows flags
- [ ] `ollama list` includes `qwen2.5-coder:7b`, `gemma4:e2b`
- [ ] `base-resume.json` exists at project root and parses as valid JSON

### Convert

- [ ] `npm run convert -- nonexistent.pdf` → `Error: File not found: ...` (clean, no stack)
- [ ] `npm run convert -- resume.doc` → clear "save as .docx" error
- [ ] `npm run convert -- resume.pdf` → extracts text, structures, shows preview, prompts to confirm, writes backup
- [ ] Existing `base-resume.json` is backed up before overwrite
- [ ] With `ollama` stopped → *"Could not reach Ollama at 127.0.0.1:11434"*
- [ ] With a scanned-image PDF → *"No text could be extracted"*

### Scan

- [ ] `npm run scan` (API only, default) completes in < 30s
- [ ] `applications/pipeline.json` exists with ranked jobs, no exact-URL or fuzzy-title duplicates
- [ ] Re-run preserves `applied`/`status`/`notes` on existing entries
- [ ] Jobs missing for > 14 days are marked `stale`
- [ ] With default config (`seniority_policy: filter`), no Senior/Staff/Principal/Manager/Director/VP/Lead titles in the pipeline
- [ ] `--seniority handicap` caps senior scores at `senior_score_cap` (default 30)
- [ ] `--seniority keep` gives senior roles their raw scores
- [ ] Pipeline length never exceeds `max_pipeline_size`
- [ ] Default output is quiet (no per-company 404s); `verbose_scan: true` re-enables them
- [ ] `npm run scan -- --sources api,linkedin` — ToS warning prints; Chromium opens; results tagged `ats_platform: "linkedin"`
- [ ] On LinkedIn block → warning printed, API results still populate, no crash

### Apply

- [ ] `npm run apply` (empty pipeline) → *"Nothing to apply to"* message
- [ ] `npm run apply` shows top-10 menu with score, company, role, ATS platform
- [ ] Menu accepts `1`–`10` → runs full flow
- [ ] Menu accepts `a` → iterates; each job confirms `y`/`n`/`q`
- [ ] `q` during apply-all cancels cleanly (no orphaned browser)
- [ ] Menu accepts `q` at top level → exits
- [ ] Invalid input → clean error, exit
- [ ] After browser close → terminal prompts *"Did you submit?"* and *"What adjustments?"*
- [ ] `feedback.md` gets a dated block with submit status + notes
- [ ] Pipeline entry's `applied` + `status` updated; SQLite status updated
- [ ] `--url <url>` bypasses menu
- [ ] `--no-autofill` skips browser, still prompts feedback (no — feedback only fires after autofill; see "Known gaps")

### Report / list / status

- [ ] `npm run report` prints the six-line summary
- [ ] `npm run list` shows tracked applications in reverse chronological order
- [ ] `npm run status -- 1 interview` updates status

### Safety rails

- [ ] Tailor output preserves every experience entry's `company` and `dates` unchanged
- [ ] Tailor never invents new experience entries or skills
- [ ] Autofill never clicks a Submit/Apply button (verify by reading [src/autofill.js](src/autofill.js))
- [ ] LinkedIn scraper never authenticates (guest endpoints only)

### Regression

- [ ] `data/base-resume.json` migration: move `base-resume.json` to `data/`, run `scan` → auto-copies back to root

## Known gaps / future features

Priority-ordered. Each is scoped enough to be a single PR.

### Near-term

1. ~~**Feedback on `--no-autofill` runs**~~ — resolved; feedback prompt fires outside the autofill block.
2. ~~**Resume tracker**~~ — resolved; `resume_hash` column added to SQLite. Hash is a 12-char SHA-256 of the tailored resume JSON, computed in `runApplyFlow` before `logApplication`.
3. ~~**Resume versioning**~~ — resolved; `--profile <name>` flag on `apply` loads `base-resume.<name>.json`. See `getResumePath()` in [tailor.js](src/apply/tailor.js).
4. **Company blacklist** — `data/blacklist.json` skipping specific slugs in scan.
5. **Company priority list** — `data/priorities.json` boosting specific slugs' fit scores.
6. **Cleaner apply-all UX** — `--limit N` and `--min-score N` exist; add `--dry-run` mode that lists what would be applied without opening browsers.

*Resolved in latest pass*: senior over-ranking (now policy-driven), multi-location duplicates (fuzzy dedup), noisy scan output (compact summary), preemptive anti-scrape (persistent browser profile + 429 backoff + login/CAPTCHA detection), cover letter "Unknown Role" bug (re-prompt + validation), tailor hallucination guardrail (Jaccard similarity ≥0.6), cliché deny-list (28 phrases, auto-retry), PROJECTS section in PDF (clickable links), company targeting reset (dropped FAANG-tier, added Toronto SaaS + agency targets), role deny-list (ROLE_DENY_RE + config `role_deny_extras`).

### Medium-term

7. **ATS coverage expansion** — Workday multi-page autofill (currently best-effort on landing page only).
9. **Interview tracker** — table for interview events, reminders, prep notes.
10. **Referral tracker** — who referred you, when, which company.
11. **Recruiter CRM** — lightweight table of recruiter contacts tied to applications.
12. **Networking tracker** — log coffee chats, follow-up reminders.

### Long-term / speculative

13. **Structured logging** — replace `process.stderr.write` with a leveled logger; write to `data/logs/`.
14. **Config file** — `config.json` for Ollama host, model choices, timeouts, default writing model.
15. **Analytics** — rendering charts of application volume, reply rate, score distribution.
16. **Fit-score learning loop** — use `feedback.md` outcomes (replies, interviews) to tune scoring weights.

## Agent working rules

When an agent (human or AI) modifies this codebase:

### Don't

- Don't add backend calls to external LLM providers (OpenAI, Anthropic, etc.). Local only.
- Don't introduce a database other than SQLite. Don't migrate to an ORM.
- Don't add a UI framework. CLI only.
- Don't write code that could auto-submit, bypass CAPTCHA, or authenticate to LinkedIn.
- Don't add features that fabricate resume content. Tailor reorders and rewords; nothing more.
- Don't add dependencies without checking bundle impact and offline availability.

### Do

- Prefer editing existing modules over creating new ones. The architecture is intentionally flat.
- Match the existing style: ES modules, top-of-file imports, small pure functions, no decorators.
- Keep LLM prompts in the module that calls them (don't centralize prompts into a registry).
- Use [src/_stream.js](src/_stream.js) helpers for every Ollama call — don't bypass the watchdog.
- Use [src/prompt.js](src/prompt.js) for interactive input — don't add a new readline path.
- Update `data/companies.json` schema cautiously; `src/companies.js` is the single loader.
- When changing the pipeline.json schema, keep old entries readable (scan.js already preserves `applied`/`status`/`notes` across shape changes).
- When touching autofill, test the close-wait path — a previous bug caused the CLI to hang forever after browser close ([src/autofill.js](src/autofill.js#L160-L168)).
- Verify PDF extraction against `pdf-parse` v2's `new PDFParse({data}).getText()` API — the v1 `lib/pdf-parse.js` import is gone.

### Style

- No emojis in user-facing output unless the user explicitly asks.
- Error messages: *what happened*, *why*, *what to do next*. One line when possible.
- Comments only for non-obvious invariants. Don't narrate what code does.
- No `try/catch` that swallows the error silently. Every catch either logs or rethrows.

### When adding a new CLI command

1. Add action to [cli.js](cli.js) using `commander`.
2. Add an `npm run <cmd>` alias to [package.json](package.json).
3. Document in [README.md](README.md) under "Commands".
4. Add QA checklist items to this file.

## Delivery standard

This project should be strong enough that:

1. it is genuinely useful to the user every week
2. it improves real hiring outcomes (measurable via `feedback.md` + `list`)
3. it holds up under portfolio review by a senior engineer
4. it demonstrates judgment about where AI helps vs. where humans decide
5. it solves an actual, painful workflow problem — not a toy demo

Build toward that standard only.
