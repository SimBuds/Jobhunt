# Instructions: how Jobhunt works end to end

This is a walkthrough of the whole app, from a blank checkout to a tracked
application. It follows the code as it runs. `README.md` covers install and
the full command reference. `AGENTS.md` (project-specific section) holds the
rules and the reasons behind each guard. `PLAN.md` holds the design.

```
resume.docx ─▶ convert-resume ─▶ kb/profile/verified.json
                                        │
config.toml ─▶ scan: ingest ─▶ filter ─▶ dedupe ─▶ DB ─▶ discover ─▶ score
                                                                      │
                          list ◀──────────────────────────────────────┘
                            │
                          apply: pick ─▶ deepen ─▶ tailor ─▶ cover ─▶ audit
                                                                       │
                          render .docx ─▶ record "drafted" ─▶ autofill ─▶ you submit
                                                                       │
                          track / apply --set-status ─▶ list, analyze, calibrate
```

Everything runs locally. The only model is the llama-server router at
`http://localhost:8080` (model `lite` in every task slot), and every model
call goes through `jobhunt.gateway`. The app never clicks Submit.

---

## 0. Prerequisites

- `uv sync`, then `uv run playwright install chromium` for autofill.
- llama-server router running as a systemd user service, with the preset in
  `~/.config/llama.cpp/models.ini` (`ctx-size = 32768`, `parallel = 1`). You
  manage that service yourself.
- A baseline resume `.docx` in the repo root with "resume" in the filename. A
  name containing "baseline" wins, otherwise the newest file wins.
  `--docx <path>` overrides.
- Optional: Adzuna API keys in `~/.config/jobhunt/secrets.toml` (mode 0600)
  or `JOBHUNT_` env vars.

## 1. First run: `jobhunt setup`

The wizard (`commands/setup_cmd.py`) runs these in order. Each one detects
existing state and offers keep or redo, so re-running it is safe.

1. **DB init.** Creates `data/jobhunt.db` and applies the numbered SQL files in
   `migrations/`.
2. **Locate the resume** (`resume/locate.py`) and confirm the choice.
3. **Convert the resume** (`resume/parse_docx.py`). Writes
   `kb/profile/verified.json` plus the markdown sidecars (`resume.md`,
   `skills.md`, `work-history.md`, `education.md`, `projects.md`). It also
   backfills empty `[applicant]` fields from the resume contact line.
   - **Write guard:** it refuses to write when the parser reports data loss,
     or when a skill bucket that had items is now empty. Fix the resume or the
     parser instead of reaching for `--force`.
4. **Applicant defaults.** Prompts for `years_experience`,
   `include_senior_roles`, salary, work arrangements and employment types.
5. **Show the resolved config** (`~/.config/jobhunt/config.toml`).
6. **Seed employers.** Previews `kb/seeds/gta-employers.toml` (verified ATS
   slugs) and offers to write them into config.

Manual equivalent: `config show`, `db init`, `convert-resume`,
`config seed --apply`.

`verified.json` is the single source of truth for everything the app says
about you. `scan`, `list`, `apply` and `resume` stop early with a pointer to
`convert-resume` when it is missing (`ensure_profile`).

## 2. Find jobs: `jobhunt scan`

`commands/scan_cmd.py`. Migrates the DB first. `--refresh` wipes the HTTP
cache and all unapplied jobs and scores before starting (application history
is kept).

### 2a. Ingest

`_ingest_all` streams every configured source concurrently, over async httpx
with a per-host rate limiter (1 req/s default, backoff on 429/5xx) and cached
raw responses in `data/cache/`.

| Source | Adapter | Notes |
|---|---|---|
| Greenhouse, Lever, Ashby, Workable, Recruitee | `ingest/<name>.py` | Public JSON APIs, keyed by slug |
| SmartRecruiters | `smartrecruiters.py` | List is summary only, so it fetches each posting's detail (skipped for titles that will be dropped anyway) |
| Workday | `workday.py` | No server-side location filter. Large boards are searched by location terms instead of a blank walk |
| Adzuna CA | `adzuna_ca.py` | API key. Queries auto-derive from `verified.json` when config lists none. Returns ~500-char snippets |
| Job Bank Canada | `job_bank_ca.py` | HTML scrape (the one sanctioned exception), 1 request per 5 s |
| Any RSS/Atom feed | `rss_generic.py` | |

LinkedIn, Indeed and Glassdoor are never scraped.

### 2b. Filter (pre-score, in the drain loop)

Each posting passes these pure checks from `ingest/_filter.py` before it is
stored. Drop counts print in the scan summary.

1. **Location:** GTA city allowlist plus Remote-Canada
   (`is_gta_eligible`). A city name that is anchored outside Canada is
   rejected.
2. **Management titles** (Manager, Director, Head of, VP, C-level) are
   dropped. Senior, Lead, Staff and Principal are not.
3. **Non-engineering titles** are dropped (default on). An engineering
   signal in the title always wins.
4. **Research/ML titles** are dropped only if you opt in.
5. **Senior titles** are dropped only when `include_senior_roles = false`.
6. **Freshness:** older than `max_age_days` (default 7) is dropped.
   `--max-age-days` overrides, and 0 turns the check off.

### 2c. Dedupe

`_dedup_decision` builds a `title:company` shadow key.

- A direct ATS row is keyed by its own id plus the shadow key. A second direct
  copy of the same role in one scan is dropped.
- An aggregator row (Adzuna, RSS, Job Bank) is keyed by the shadow key only,
  so it drops when any earlier row claimed it.
- When a direct row arrives after an aggregator copy in the same scan, the
  thinner aggregator row is deleted. Direct rows win because their
  descriptions are complete.

Survivors are upserted into `jobs`.

### 2d. Auto-discover

If new rows were inserted and `auto_discover` is on (turn it off with
`--no-discover`), `discover/probe.py` guesses ATS slugs for newly seen
companies, probes the public APIs, and appends hits to `config.toml`. Later
scans then pull the full description directly from the ATS. It is skipped
while the ready-to-apply backlog is above `discover_backlog_ceiling`. Config
writes keep a `.bak` copy but drop inline comments.

### 2e. Score

`db.jobs_to_score` selects jobs that were never scored, or were scored under
a different `prompt_hash`. The hash covers `kb/prompts/score.md`,
`verified.json`, `kb/policies/tailoring-rules.md`, all score weights and the
score model. Editing any of those re-scores the backlog on the next scan.

For each job, `pipeline/score.py:score_job`:

1. Scrubs the description (`_untrusted.scrub_jd`). Hidden characters are
   deleted, and instruction-like text aimed at the model is replaced with a
   visible `[redacted]` marker. The description is truncated to 16000 chars.
2. Sends one schema-bound call. **The model does not choose the score.** It
   returns `must_haves` (tier 1), `nice_to_haves` (tier 2), an AI bonus flag
   and an optional `decline_reason`.
3. Drops pure tenure asks ("7+ years of experience") and collapses duplicate
   bridged phrases. If tier 1 is empty, tier 2 is promoted to tier 1.
4. Re-checks every phrase against `verified.json`: literal match, peer family,
   or a `(transferable: X)` bridge where X itself verifies. A match the model
   invented becomes a gap.
5. Computes
   `score = 30 + 50 × tier1_coverage + 10 × tier2_coverage + 5 (AI bonus)`.
   An exact match counts 1.0, a bridged match counts 0.7, and an explicit
   junior title adds 5.
6. Applies caps. Caps can only lower the score:
   - **Thin JD** (under 800 chars): capped at 70.
   - **Senior title:** capped at 45, below `min_score`, so senior roles stay
     in the DB but never reach the queue.
   - **Familiar-only fit:** 54 plus a decline for senior titles, 58 for others.
     This cap does nothing while `skills_familiar` is empty.
7. Clears declines the evidence does not support (`_decline_guards`): a
   Senior-band decline on a junior title, a Familiar decline against an empty
   bucket, and years or management declines that the JD text does not
   back up.
8. Writes to `scores`: the score, matched phrases, gaps, the decline, and a
   `breakdown` holding the computed and final values plus which caps fired.
   The decline is also saved on the job.

A failure on one job (HTTP error, context overflow, runaway generation) is
logged and skipped. There is no HTTP retry, so the job is scored again on the
next scan.

## 3. Review: `jobhunt list`

`commands/list_cmd.py` shows a summary line (ready to apply, drafted but not
submitted, no reply after 14 days), then the top unapplied targets, then the
weekly funnel. **Ready to apply** means: score ≥ `min_score` (default 55),
not declined, and no application row yet or one still at `drafted`.

Filters: `--min-score`, `--applied`, `--drafted`, `--withdrawn`, `--week N`,
`--verdict`, `--no-reply --older-than 14d`, `--limit`.

## 4. Draft and apply: `jobhunt apply`

`commands/apply_cmd.py:run`. Pick one way to choose jobs:

| Mode | What it does |
|---|---|
| `apply <job-id>` | One job. Also accepts a unique company/title substring (`_refs.resolve_job_ref`) |
| `apply --top N` | The N best-scoring ready jobs (1 to 20) |
| `apply --best` | Interactive picker over the top 10. `--include-borderline` adds up to 10 stretch jobs scoring `min_score-10` to `min_score-1` |
| `apply --url <URL>` | Ad-hoc. Checks robots.txt, fetches the page and scores it first (`--no-score` skips scoring, `--stdin` takes the JD from a paste). Afterwards it suggests `jobhunt add` for the employer |

`--no-browser` produces documents only. `--min-score` changes the selection
floor.

### Per-job pipeline

`_apply_each` runs the jobs one after another. While you review job N, it
already runs job N+1's model work in the background. Its output is held and
printed when that job's turn comes.

**LLM phase** (`_apply_llm_phase`):

1. **Deepen thin Adzuna rows.** If the description is under 800 chars, it
   follows the tracking redirect, checks robots.txt, and fetches the full
   posting. On success the description is updated and the old snippet score
   is deleted, so the next scan re-scores the job.
2. **Tailor the resume** (`tailor.tailor_resume_with_retry`, temperature 0.3):
   - One schema-bound call built from `verified.json`, the policy and the
     scrubbed JD. The JD decides what is selected and in what order. It never
     adds content.
   - `_enforce_no_fabrication` rejects any role, employer, date or skill that
     is not in `verified.json`. On a violation, it retries up to 3 times at
     temperature 0 with a hint naming the violation. If every attempt fails,
     the job is skipped. An unverified resume is never shipped.
   - Post-processing: dedupe education, complete the Familiar bucket, add back
     verified skills the JD requires, cap the lead skill category at 10 items,
     then shrink to one page. Shrinking trims the summary, then the Familiar
     list, then the weakest bullets, then coursework. If the resume still
     overflows, the job fails.
3. **Write the cover letter** (`cover.write_cover_with_retry`, temperature
   0.7). `cover_validate` checks for banned or defensive phrases, unverified
   numbers, overreaching claims, word and paragraph counts, and the company
   name in the opening. Violations trigger a retry with a hint, up to the
   configured attempt count. After that, two narrow automatic patches are
   tried (add the company to the opening, swap banned phrases). If issues
   remain, the draft is kept and the audit marks it `revise`.
4. **Audit** (`pipeline/audit.py`, no model call). It checks:
   - Keyword coverage of the JD must-haves: under 70% is `revise`, under 50%
     is `block`.
   - Fabrication re-check: any failure is `block`.
   - Hidden text or injected instructions in the resume or cover: `block`.
     Hits that came from the posting itself are only logged.
   - Resume and cover naming different anchor projects: `revise`.
   - Specificity: fewer than 40% of the resume's numbers kept: `revise`.
   - Cover validator violations: `revise`.

   Writes `audit.json` and `tailor-diff.md`. On `block`, the job stops here.

**IO phase** (`_apply_io_phase`):

5. Prints the `revise` warnings.
6. **Renders** `<Name>_Resume.docx`, `<Name>_Cover_Letter.docx`,
   `cover-letter.md` and `tailored-resume.json` into
   `data/applications/<job-id>/`.
7. **Records `drafted`** in `applications` immediately, so a later crash or
   Ctrl-C does not lose the drafts.
8. **Autofill** (`browser/autofill.py`, unless `--no-browser` or the job has
   no URL). Opens a visible Chromium window with a persistent profile, loads
   the page and chooses a handler from the final URL after redirects
   (Greenhouse, Lever, Ashby, Workday, or generic). If an application form is
   detected, it fills the fields and uploads both .docx files. Either way it
   writes `fill-plan.json`. It never clicks Submit, creates accounts or
   stores logins. The browser stays open until you close it. If the step
   fails, you can retry or skip.
9. **You submit**, then answer `did you submit? [y/n/w]`. The status becomes
   `applied`, `drafted` or `withdrawn`. Without an interactive terminal, the
   status stays `drafted`.
10. Between jobs it asks `continue? [Y/n]`. A multi-job run ends with a
    summary of ship, revise and block counts and the most common warning
    types.

## 5. Supporting commands while applying

- **`jobhunt answer "<question>" [--job <id>]`**: drafts an answer to a form
  question under the cover-letter honesty rules (200 words by default).
  Prints it and saves it under `data/applications/<id>/answers/` or
  `data/answers/`. `--recall` reuses saved answers.
- **`jobhunt add <URL>`**: turns an employer job URL into an ATS slug and
  writes it to config.
- **`jobhunt resume --focus ai|cms|all`**: builds general resumes for each
  track ("lane") from the `kb/lanes/*.md` briefs into `data/resumes/`.
  `scripts/audit_lane_resumes.py` runs the audit on them.

## 6. After you apply: lifecycle tracking

Pipeline applications:

```bash
jobhunt apply --set-status applied <ref>
jobhunt apply --mark-response <date> --recruiter-type internal_recruiter <ref>
jobhunt apply --mark-interview <date> <ref>
jobhunt apply --set-outcome offer|rejected|withdrawn|ghosted <ref>
```

Applications made outside the pipeline, with no model call (`track_cmd.py`):

```bash
jobhunt track applied --channel linkedin --paste   # or <id>, a URL, --jd-from-stdin, --no-jd
jobhunt track response|interview|outcome <ref> [--when <date>]
jobhunt track sweep [--older-than 21d] [--apply]   # the only thing that marks silence as ghosted
```

Both paths use `_run_lifecycle`. Re-tailoring a job never changes a channel
you set manually.

**`jobhunt interview-prep <ref> [--stage ...] [--research]`**: fixed sections
(header, compensation, checklist) plus one model call for the role summary,
talking points, likely questions, questions to ask and honest gaps. It runs
the same honesty checks and writes `data/interview-prep/<id>.md`.

## 7. Weekly review and tuning

Every command below except `config reprobe` works only from the local DB,
with no model call. `config reprobe` re-checks the configured slugs against
the ATS APIs.

```bash
jobhunt list --week 0
jobhunt list --no-reply --older-than 14d
jobhunt track sweep --apply
jobhunt config reprobe --prune          # drop dead slugs
jobhunt analyze funnel --by channel
jobhunt analyze response-rate --by score
jobhunt analyze certs --trend --min-score 55
jobhunt analyze employers | skills | validators
jobhunt config calibrate                # after ~20 applications: tune min_score and weights
```

`config calibrate` reports interview rate by score band and by tier-1
coverage. Tune against coverage, because the caps squash different fits onto
the same score.

## 8. Maintenance and recovery

- **Re-score everything:** edit the score prompt, policy, profile or weights
  (they change the hash), or run `scan --refresh`. A code-only change to
  `score.py` does not change the hash.
- **Resume changed:** run `convert-resume`, then `scan` (the profile hash
  triggers re-scoring), then `resume --focus all`.
- **`jobhunt db gc`** prunes old data. **`jobhunt db reset`** deletes the DB,
  drafts, cache, prep docs, answers, the browser profile and **all of
  `kb/profile/`**, including the hand-written gitignored notes. Back that
  folder up first. Then run `jobhunt setup`.
- **Checks:** `uv run pytest -q`, `uv run ruff check`,
  `uv run mypy --strict src/`. Tests make no network or model calls. After
  changing a prompt or the model, check pipeline quality by hand with
  `scripts/eval_tailor.py`. Check autofill with `apply --no-browser` first,
  then a normal browser run.

## Where things live

| Path | Contents |
|---|---|
| `~/.config/jobhunt/config.toml` | Sources, applicant, pipeline weights, gateway models |
| `~/.config/jobhunt/secrets.toml` | API keys (0600) |
| `kb/profile/verified.json` | Verified facts that all generation draws from |
| `kb/prompts/*.md` | Task prompts with schema and temperature frontmatter |
| `kb/policies/tailoring-rules.md` | Rules injected into prompts (part of the score hash) |
| `data/jobhunt.db` | `jobs`, `scores`, `applications`, `slug_probes`, answer index |
| `data/applications/<id>/` | `.docx` files, `cover-letter.md`, `tailored-resume.json`, `audit.json`, `tailor-diff.md`, `fill-plan.json`, `answers/` |
| `data/resumes/`, `data/interview-prep/`, `data/answers/`, `data/cache/` | Track resumes, prep docs, saved answers, HTTP cache |
