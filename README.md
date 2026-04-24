# job-agent

**AI-assisted personal job search operating system.**

Discover, score, tailor, and apply — with a human in the loop for every submission. Everything runs locally; no resume or job data leaves your machine.

This is *not* an auto-apply bot. You click submit.

## Commands at a glance

```bash
npm run convert -- resume.pdf     # (first-run) build base-resume.json
npm run scan                      # discover + score into applications/pipeline.json
npm run apply                     # pick from the top-10 menu or apply-to-all
npm run report                    # weekly summary
npm run list                      # tracked applications
npm run status -- 1 interview     # update an application's status
```

All commands also work as `node cli.js <cmd>` directly. `npm run` passes flags through after `--`:

```bash
npm run apply -- --fast
npm run scan -- --sources api,linkedin,indeed
```

## First run

1. Drop your resume into the project root as `resume.pdf` or `resume.docx`.
2. Run `npm run convert`. It auto-detects the file, extracts text (via `pdf-parse` / `mammoth`), and calls `qwen2.5-coder:7b` to structure it into [base-resume.json](base-resume.json). It will never invent data — missing fields become empty strings/arrays. Any existing `base-resume.json` is backed up before overwrite.
3. Review `base-resume.json` and fill in anything the LLM missed. The tailor can only reorder and reword *existing* bullets — the richer your base resume, the better the output.

If you run `scan` or `apply` with no `base-resume.json`, the CLI auto-detects a resume file and offers to convert it for you.

## Commands

### `scan` — discover + score

Pulls live postings and writes them ranked into `applications/pipeline.json`. Re-runs are safe: dedupes by URL, preserves `applied`/`status`/`notes`, and marks postings missing >14 days as `stale`.

**Sources** (comma-separated via `--sources`, default `api`):

| Source | Type | ToS | Notes |
|---|---|---|---|
| `api` | Greenhouse + Lever public APIs | Safe | Fast, reliable. Watchlist in [data/companies.json](data/companies.json). |
| `jobbank` | Government of Canada (jobbank.gc.ca) | Safe | Public board. Deep coverage of Ontario roles incl. part-time/contract. |
| `linkedin` | LinkedIn guest search | Grey | Stealth Chromium. May rate-limit. |
| `indeed` | Indeed.ca | Grey | Stealth Chromium. CAPTCHA-challenges over time (cookies persist). |
| `workopolis` | Workopolis.com | Grey | Explicit opt-in only. Site dormant since 2018 — expect 0 results. |
| `civicjobs` | CivicJobs.ca | Blocked | Explicit opt-in only. Cloudflare-protected — returns 0 results. |
| `all` | Shortcut | — | Runs `api + linkedin + indeed + jobbank` in sequence. |

All searches are centered on **Toronto, ON with a 100km radius** (covers Mississauga, Hamilton, Kitchener-Waterloo, Barrie, Oshawa, Niagara, etc.) and cover full-time / part-time / contract / internship / hybrid / remote / on-site.

```bash
npm run scan                                     # API only (default, safest)
npm run scan -- --sources api,jobbank            # + Job Bank
npm run scan -- --sources all                    # api + linkedin + indeed + jobbank
```

> ⚠ **LinkedIn and Indeed scraping may violate their ToS.** The agent uses realistic UA, jittered delays, guest endpoints only, and caps each source at 3 queries × 25 results per run. Cookies persist in `data/browser-profile/` so CAPTCHA challenges don't repeat every run.

Scoring is deterministic (no LLM) across: keyword overlap, tech-stack overlap, title/seniority fit, education relevance, ATS keyword density. Multi-location duplicates are merged via a fuzzy `company::role` key (keeping the higher-scored entry). Missing keywords are written into each entry's `notes`.

**Seniority policy** — controlled by [data/config.json](data/config.json) or `--seniority` on the CLI:

- `filter` (default): drop Senior/Staff/Principal/Manager/Director/VP/Lead/Architect titles entirely
- `handicap`: keep them but cap their score at `senior_score_cap` (default 30)
- `keep`: no penalty

```bash
npm run scan                              # uses config default (filter)
node cli.js scan --seniority handicap     # one-off override
```

Tune queries in [data/companies.json](data/companies.json):

```json
{
  "greenhouse": ["shopify", "cohere", "wealthsimple"],
  "lever": ["ritual", "kepler"],
  "linkedin_queries": ["software engineer", "frontend developer"],
  "indeed_queries":   ["software engineer", "backend developer"]
}
```

### `apply` — guided apply

```bash
npm run apply
```

Prints the top 10 unapplied jobs and asks:

```
Top unapplied roles:

   1.  93% — hootsuite — Junior Software Developer, Frontend [greenhouse]
   2.  78% — kepler — Embedded Software Test Automation Designer [lever]
   ...
  10.  72% — hootsuite — Co-op/Intern, Internal AI Operations [greenhouse]

   a. Apply to all listed above (one at a time, with confirm)
   q. Cancel

Choose [1-10, a, q]:
```

- **Number**: runs the full flow for that one job.
- **a**: loops through all 10. Before each job you're asked `Proceed with this one? (y/n/q)` — `y` runs the flow, `n` skips, `q` cancels the batch.
- **q**: exit.

Each apply flow:

1. Scrape the posting
2. Analyze with `qwen2.5-coder:7b`
3. Tailor resume (rewrite summary, reorder existing bullets — never invents experience)
4. Draft cover letter (~250 words, matching tone)
5. Render PDFs to `output/`
6. Log to SQLite + update pipeline
7. Launch Chromium with autofilled contact fields — **you** review and click submit
8. Close the browser → CLI prompts *"Did you submit? (y/n)"* then *"Any adjustments or issues?"* → append to `feedback.md`, mark the pipeline entry

Flags:

- `--url <url>` — skip the menu, apply to a specific URL
- `--no-autofill` — skip the browser step (just produce PDFs and log)
- `--fast` — use `qwen3.5:4b` for writing steps

### `report` — weekly summary

```bash
npm run report
```

```
This Week

  12 strong matches found
  5 applications submitted
  3 resume adjustments needed
  2 jobs worth skipping
  Top target: Shopify Backend Intern (92%)
  Next target: Wealthsimple SWE Intern (88%)
```

### `convert` — resume → base-resume.json

```bash
npm run convert                   # auto-detects resume.pdf / resume.docx
npm run convert -- resume.pdf     # explicit path
npm run convert -- -y resume.pdf  # skip the confirmation prompt
```

Supports `.pdf`, `.docx`, `.txt`, `.md`. Shows a preview of what it extracted and asks before overwriting. Clear errors if the file can't be read, the model isn't pulled, or Ollama isn't running.

### `list` / `status`

```bash
npm run list
npm run status -- 3 interview
```

## Models

| Model | Role | Used by |
|---|---|---|
| `qwen2.5-coder:7b` | Structured JSON extraction | analyze, convert |
| `gemma4:e2b` | Resume tailoring + cover letter (default) | tailor, coverletter |
| `qwen3.5:4b` | Fast fallback for writing (`--fast`) | tailor, coverletter |

All LLM calls stream with a 45s stall watchdog and 5-minute hard cap. Ollama host: `http://127.0.0.1:11434`.

## Prerequisites

- **Node.js** 18+
- **Ollama** with models pulled:
  ```bash
  ollama pull qwen2.5-coder:7b
  ollama pull gemma4:e2b
  ollama pull qwen3.5:4b   # optional, only for --fast
  ```
- **Playwright browsers** (for autofill + LinkedIn/Indeed scan):
  ```bash
  npx playwright install chromium
  ```

## Setup

```bash
npm install
```

## Supported ATS platforms

| Platform | Scrape (apply) | Autofill | Scan source |
|---|---|---|---|
| Greenhouse | Yes | Yes | API |
| Lever | Yes | Yes | API |
| LinkedIn | Yes | No | Stealth browser (opt-in) |
| Indeed | Yes | No | Stealth browser (opt-in) |
| Workday | Yes | Best-effort | — |
| Ashby | Yes | Yes | — |
| SmartRecruiters | Yes | Yes | — |
| iCIMS | Yes | No | — |

Workday autofill is best-effort — the form spans multiple pages and usually requires sign-in first. The agent fills what's visible on the landing page.

## Configuration

Tunable behavior lives in [data/config.json](data/config.json):

| Key | Default | Meaning |
|---|---|---|
| `seniority_policy` | `"filter"` | `filter` / `handicap` / `keep` — see above |
| `senior_score_cap` | `30` | Max score for senior roles when policy is `handicap` |
| `max_pipeline_size` | `200` | Trim the lowest-scoring entries beyond this |
| `verbose_scan` | `false` | Set `true` to re-enable per-company 404 / error logs |

Scraper cookies persist in `data/browser-profile/` (gitignored) so LinkedIn/Indeed don't re-issue the same challenges every scan. Delete the folder to reset the session.

## Output

- PDFs: `output/resume-{company}-{date}.pdf`, `output/coverletter-{company}-{date}.pdf`
- Pipeline: `applications/pipeline.json`
- Feedback log: `feedback.md`
- History: `data/applications.db` (SQLite)

## Safety rails

- **Never** auto-submits — you click the submit button
- **Never** bypasses CAPTCHA or logins
- **Never** invents experience, metrics, or projects
- **LinkedIn/Indeed scraping is ToS-grey** — opt-in, rate-limited, best-effort

## Troubleshooting

- **LinkedIn/Indeed scan returns zero jobs** — you've been rate-limited. Wait a few hours or skip those sources. The CLI prints a warning and falls back to API results.
- **Indeed CAPTCHA page opens** — solve it manually in the open browser; future runs from that IP may work for a while.
- **Browser hangs open after submit** — close the Chromium window; the CLI immediately prompts for feedback.
- **`fetch failed` / `timed out`** — target site blocked the request. Scrape has a 20s timeout. Open the URL manually if persistent.
- **`Could not read PDF`** — export the resume as `.docx` or `.txt` and re-run `convert`.
- **`model not found`** — `ollama pull` the model in the error.
- **`Could not reach Ollama`** — start it with `ollama serve`.
