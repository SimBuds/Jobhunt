# Casey's Personal Job Seeker

**AI-assisted personal job search operating system.**

Discover, score, tailor, and apply — with a human in the loop for every submission. Everything runs locally; no resume or job data leaves your machine.

This is *not* an auto-apply bot. You click submit.

---

## Quick start

```bash
npm install
npm run convert -- resume.pdf   # build base-resume.json from your resume
npm run scan                    # discover + score jobs into pipeline.json
npm run apply                   # pick a job and run the apply flow
```

**Prerequisites:**

- **Node.js** 18+
- **Ollama** running with models pulled:
  ```bash
  ollama pull qwen2.5-coder:7b
  ollama pull gemma4:e2b          # tailor only
  ollama pull qwen3.5:4b          # optional, only for --fast
  ```
- **Playwright Chromium** (for autofill + LinkedIn scan):
  ```bash
  npx playwright install chromium
  ```

---

## First run

1. Drop your resume into the project root as `resume.pdf` or `resume.docx`.
2. Run `npm run convert`. It auto-detects the file, extracts text, and calls `qwen2.5-coder:7b` to structure it into `base-resume.json`. It will never invent data — missing fields become empty strings/arrays. Any existing `base-resume.json` is backed up first.
3. Review `base-resume.json` and fill in anything the LLM missed. The tailor can only reorder and reword *existing* bullets — the richer your base resume, the better the output.

If you run `scan` or `apply` with no `base-resume.json`, the CLI auto-detects a resume file and offers to convert it for you.

---

## Commands

```bash
npm run convert -- resume.pdf     # build base-resume.json
npm run scan                      # discover + score into applications/pipeline.json
npm run apply                     # guided apply flow
npm run report                    # weekly summary
npm run list                      # tracked applications
npm run status -- 1 interview     # update an application's status
```

All commands also work as `node cli.js <cmd>`. Pass flags after `--`:

```bash
npm run apply -- --fast --profile frontend
npm run scan -- --sources api,linkedin
```

---

## Commands in detail

### `scan` — discover + score

Pulls live postings and writes them ranked into `applications/pipeline.json`. Re-runs are safe: dedupes by URL, preserves `applied`/`status`/`notes`, and marks postings missing >14 days as `stale`.

**Sources** (comma-separated via `--sources`, default `api`):

| Source | Type | ToS | Notes |
|---|---|---|---|
| `api` | Greenhouse + Lever public APIs | Safe | Fast, reliable. Watchlist in [data/companies.json](data/companies.json). |
| `jobbank` | Government of Canada (jobbank.gc.ca) | Safe | Public board. Deep coverage of Ontario roles incl. part-time/contract. |
| `linkedin` | LinkedIn guest search | Grey | Stealth Chromium. May rate-limit. |
| `all` | Shortcut | — | Runs `api + linkedin + jobbank` in sequence. |

All searches are centered on **Toronto, ON with a 100km radius** (covers Mississauga, Hamilton, Kitchener-Waterloo, Barrie, Oshawa, Niagara, etc.) and cover full-time / part-time / contract / internship / hybrid / remote / on-site.

```bash
npm run scan                              # API only (default, safest)
npm run scan -- --sources api,jobbank     # + Job Bank
npm run scan -- --sources all             # api + linkedin + jobbank
```

> ⚠ **LinkedIn scraping may violate their ToS.** The agent uses a realistic UA, jittered delays, and guest endpoints only, capped at 3 queries × 25 results per run. Cookies persist in `data/browser-profile/` to avoid repeat challenges.

Indeed was dropped as a source — both their HTML pages and RSS feed are now Cloudflare-blocked without paid proxy infrastructure. LinkedIn + Job Bank + Greenhouse/Lever APIs cover the same roles.

**Scoring** is deterministic (no LLM) across: keyword overlap, tech-stack overlap, title/seniority fit, education relevance, ATS keyword density. Multi-location duplicates are merged via a fuzzy `company::role` key. Missing keywords are written into each entry's `notes`.

**Seniority policy** — controlled by [data/config.json](data/config.json) or `--seniority` on the CLI:

- `filter` (default): drop Senior/Staff/Principal/Manager/Director/VP/Lead/Architect titles entirely
- `handicap`: keep them but cap their score at `senior_score_cap` (default 30)
- `keep`: no penalty

```bash
npm run scan                              # uses config default (filter)
node cli.js scan --seniority handicap     # one-off override
```

Tune the company watchlist and search queries in [data/companies.json](data/companies.json):

```json
{
  "greenhouse": ["shopify", "cohere", "wealthsimple"],
  "lever": ["ritual", "kepler"],
  "linkedin_queries": ["software engineer", "frontend developer"],
  "jobbank_queries":  ["software developer", "web developer"]
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
- **a**: loops through all 10; before each job you confirm `y` / `n` (skip) / `q` (cancel batch).
- **q**: exit.

Each apply flow:

1. Scrape the posting
2. Analyze with `qwen2.5-coder:7b`
3. Tailor resume (rewrite summary, reorder existing bullets — never invents experience)
4. Draft cover letter (~250 words, matching tone)
5. Render PDFs to `output/`
6. Log to SQLite + update pipeline
7. Launch Chromium with autofilled contact fields — **you** review and click submit
8. Close the browser → CLI prompts *"Did you submit?"* and *"Any adjustments?"* → appended to `feedback.md`

**Flags:**

| Flag | Description |
|---|---|
| `--url <url>` | Skip the menu and apply to a specific URL |
| `--limit <n>` | Menu size (default 10) |
| `--min-score <n>` | Hide candidates below this fit score |
| `--no-autofill` | Skip browser autofill; still produces PDFs and logs |
| `--fast` | Use `qwen3.5:4b` for writing steps |
| `--profile <name>` | Load `base-resume.<name>.json` instead of `base-resume.json` |

Resume profiles let you maintain separate tailored bases for different tracks:

```bash
cp base-resume.json base-resume.frontend.json
npm run apply -- --profile frontend
```

### `convert` — resume → base-resume.json

```bash
npm run convert                   # auto-detects resume.pdf / resume.docx
npm run convert -- resume.pdf     # explicit path
npm run convert -- -y resume.pdf  # skip confirmation prompt
```

Supports `.pdf`, `.docx`, `.txt`, `.md`. Shows a preview and asks before overwriting. Backs up any existing `base-resume.json` first.

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

### `list` / `status`

```bash
npm run list
npm run status -- 3 interview
```

---

## Project structure

```
cli.js                        # Entry point — all commands via commander
base-resume.json              # Your source-of-truth resume (never auto-modified)
base-resume.<profile>.json    # Optional profile variants (--profile flag)

src/
  apply/
    scrape.js                 # Platform-aware job description extraction (cheerio, 24h cache)
    analyze.js                # qwen2.5-coder:7b → structured requirements JSON
    tailor.js                 # gemma4:e2b → rewrite summary, reorder bullets
    coverletter.js            # ~250-word cover letter draft
    render.js                 # pdf-lib → PDFs in output/
    autofill.js               # Playwright headful — fills Greenhouse/Lever/Ashby/SmartRecruiters/Workday
  discover/
    scan.js                   # Orchestrates sources, dedupes, scores, writes pipeline.json
    score.js                  # Deterministic fit score (no LLM)
    sources/
      browser-search.js       # Stealth Playwright launcher (UA spoof, jitter, 429 backoff)
      linkedin.js             # LinkedIn guest-search parser
      jobbank.js              # Government of Canada Job Bank parser
  core/
    config.js                 # data/config.json loader
    companies.js              # data/companies.json loader
    track.js                  # SQLite — log and query applications
    feedback.js               # Append-only feedback.md writer
    prompt.js                 # readline ask/askYesNo helpers
    stream.js                 # Ollama stream watchdog (45s stall, 5min cap) + retry
  convert.js                  # PDF/DOCX → base-resume.json
  report.js                   # CLI dashboard

data/
  companies.json              # Company watchlist + per-source query lists
  config.json                 # Tunable behavior (seniority policy, pipeline cap, verbosity)
  applications.db             # SQLite history (gitignored)
  browser-profile/            # Persistent Chromium profile for LinkedIn cookies (gitignored)

applications/
  pipeline.json               # Scored, ranked job postings (output of scan)

output/                       # Generated PDFs (gitignored)
  resume-{company}-{date}.pdf
  coverletter-{company}-{date}.pdf

feedback.md                   # Post-apply notes, append-only
```

---

## Models

| Model | Role | Used by |
|---|---|---|
| `qwen2.5-coder:7b` | Structured JSON extraction + cover letter | analyze, convert, coverletter |
| `gemma4:e2b` | Resume tailoring (default) | tailor |
| `qwen3.5:4b` | Fast fallback (`--fast`) | tailor, coverletter |

All LLM calls stream through a watchdog: 45s stall timeout, 5-minute hard cap. Ollama host: `http://127.0.0.1:11434`.

---

## Configuration

[data/config.json](data/config.json):

| Key | Default | Meaning |
|---|---|---|
| `seniority_policy` | `"filter"` | `filter` / `handicap` / `keep` — see Scan section |
| `senior_score_cap` | `30` | Max score for senior roles when policy is `handicap` |
| `max_pipeline_size` | `200` | Trim the lowest-scoring entries beyond this |
| `verbose_scan` | `false` | Set `true` to re-enable per-company 404 / error logs |
| `role_deny_extras` | `[]` | Extra role title substrings to block, e.g. `["staff accountant"]` |

---

## Supported ATS platforms

| Platform | Scrape (apply) | Autofill | Scan source |
|---|---|---|---|
| Greenhouse | Yes | Yes | API |
| Lever | Yes | Yes | API |
| LinkedIn | Yes | No | Stealth browser (opt-in) |
| Indeed | Yes | No | — |
| Workday | Yes | Best-effort | — |
| Ashby | Yes | Yes | — |
| SmartRecruiters | Yes | Yes | — |
| iCIMS | Yes | No | — |

Workday autofill is best-effort — the form spans multiple pages and usually requires sign-in first.

---

## Safety rails

- **Never** auto-submits — you click the submit button
- **Never** bypasses CAPTCHA or logins
- **Never** invents experience, metrics, or projects
- **LinkedIn scraping is ToS-grey** — opt-in, rate-limited, best-effort

---

## Troubleshooting

- **LinkedIn scan returns zero jobs** — you've been rate-limited. Wait a few hours or switch to `--sources api,jobbank`.
- **Browser hangs open after submit** — close the Chromium window; the CLI immediately prompts for feedback.
- **`fetch failed` / `timed out`** — target site blocked the request. The scraper has a 20s timeout. Open the URL manually if persistent.
- **`Could not read PDF`** — export the resume as `.docx` or `.txt` and re-run `convert`.
- **`model not found`** — run `ollama pull <model-name>`.
- **`Could not reach Ollama`** — start it with `ollama serve`.
