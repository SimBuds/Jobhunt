# job-agent

**AI-assisted personal job search operating system.**

Discover, score, tailor, and apply — with a human in the loop for every submission. Everything runs locally; no job or resume data leaves your machine.

This is *not* an auto-apply bot. You click submit.

## Workflow

```bash
job-agent scan              # discover + score jobs into pipeline.json
job-agent apply             # guided apply on the highest-fit unapplied role
job-agent digest            # weekly CLI summary
```

Supporting commands:

```bash
job-agent import-resume resume.pdf   # convert PDF/DOCX -> base-resume.json
job-agent list                       # show tracked applications
job-agent status <id> <new_status>   # update status (interview, offer, rejected)
```

## Commands

### `scan` — discover + score

Pulls live postings from the Greenhouse and Lever public job APIs for companies listed in [data/companies.json](data/companies.json). Filters to software/engineering roles in Toronto / GTA / Remote Canada. Scores every posting against your [base-resume.json](base-resume.json) and writes the ranked pipeline to `applications/pipeline.json`.

Re-running is safe — it dedupes by URL, preserves `applied`/`status`/`notes` on existing entries, and marks postings that disappear for more than 14 days as `stale`.

Scoring is deterministic (no LLM) across: keyword overlap, stack overlap, title/seniority fit, education relevance, ATS keyword density. Missing keywords are surfaced in each entry's `notes`.

Edit [data/companies.json](data/companies.json) to tune the company watchlist:

```json
{
  "greenhouse": ["shopify", "cohere", "wealthsimple"],
  "lever": ["ritual", "kepler"]
}
```

### `apply` — guided apply flow

With no arguments, picks the highest-fit unapplied role from the pipeline and confirms before proceeding. Pass `--url <url>` to target a specific posting.

The flow:

1. **Scrape** the job description ([src/scrape.js](src/scrape.js))
2. **Analyze** with `qwen2.5-coder:7b` — extracts structured JSON ([src/analyze.js](src/analyze.js))
3. **Tailor** resume: rewrite summary, reorder existing bullets toward the JD ([src/tailor.js](src/tailor.js)). Never invents experience.
4. **Cover letter**: ~250 words in the matching tone ([src/coverletter.js](src/coverletter.js))
5. **Render** `resume-{company}-{date}.pdf` and `coverletter-{company}-{date}.pdf` ([src/render.js](src/render.js))
6. **Log** to SQLite ([src/track.js](src/track.js))
7. **Autofill** — launches Chromium, pre-fills contact fields for Greenhouse / Lever / Ashby / SmartRecruiters / Workday ([src/autofill.js](src/autofill.js)). **You review and click submit.**
8. **Close the browser window** when done. The CLI then prompts:
   - *Did you complete and submit the application? (y/n)*
   - *What adjustments were needed / prevented submission?*
9. Feedback is appended to [feedback.md](feedback.md) and the pipeline entry is updated.

Flags:

- `--url <url>` — apply to a specific URL instead of the pipeline top
- `--no-autofill` — skip browser, just produce PDFs and log
- `--fast` — use `qwen3.5:4b` for writing steps

### `digest` — weekly summary

```text
This Week

  12 strong matches found
  5 applications submitted
  3 resume adjustments needed
  2 jobs worth skipping
  Top target: Shopify Backend Intern (92%)
  Next target: Wealthsimple SWE Intern (88%)
```

Reads from `applications/pipeline.json`, `feedback.md`, and the SQLite log.

### `import-resume` — bootstrap base-resume.json

```bash
node cli.js import-resume resume.pdf
node cli.js import-resume resume.docx
```

Extracts text (via `pdf-parse` or `mammoth`), then asks `qwen2.5-coder:7b` to convert it into the `base-resume.json` schema. Never invents data — missing fields become empty strings/arrays. Existing `base-resume.json` is backed up before overwrite.

Pass `-y` to skip the confirmation prompt.

## Models

| Model | Role | Used by |
|---|---|---|
| `qwen2.5-coder:7b` | Structured JSON extraction | analyze, import-resume |
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
- **Playwright browsers** (only for autofill):
  ```bash
  npx playwright install chromium
  ```

## Setup

```bash
npm install
```

Then populate your resume — either by hand-editing [base-resume.json](base-resume.json) or by running:

```bash
node cli.js import-resume path/to/your-resume.pdf
```

The tailor reorders and rewords existing bullets — it does **not** invent experience — so the more detailed your base resume, the better the output.

## Supported ATS platforms

| Platform | Scrape | Autofill | Scan (via API) |
|---|---|---|---|
| Greenhouse | Yes | Yes | Yes |
| Lever | Yes | Yes | Yes |
| Workday | Yes | Best-effort | No (no public API) |
| Ashby | Yes | Yes | No |
| SmartRecruiters | Yes | Yes | No |
| iCIMS | Yes | No | No |
| LinkedIn | Yes | No | No |
| Other career pages | Best-effort | No | No |

**Workday autofill** is best-effort: the form spans multiple pages and usually requires sign-in. The agent fills what's visible on the landing page.

**Scan** only covers Greenhouse + Lever APIs (compliant, no ToS issues). For companies on other ATSs, use `apply --url <url>` directly.

## Output

- PDFs: `output/resume-{company}-{date}.pdf`, `output/coverletter-{company}-{date}.pdf`
- Pipeline: `applications/pipeline.json`
- Feedback log: `feedback.md`
- History: `data/applications.db` (SQLite) — schema: `applications(id, company, role, url, applied_at, status, resume_path, coverletter_path, notes)`

## Safety rails

- **Never** auto-submits — you click the final submit button in the browser
- **Never** bypasses CAPTCHA or logins
- **Never** invents experience, metrics, or projects in tailoring
- **Never** scrapes LinkedIn / Indeed search results (ToS compliance)

## Troubleshooting

- **Browser hangs open after submit** — close the Chromium window; the CLI will immediately prompt for feedback.
- **`fetch failed` / `Fetch timed out`** — the target site blocked the request. Scrape has a 20s timeout. Open the URL manually if persistent.
- **`Failed to extract JSON from tailor response`** — the writing model returned prose. Re-run; if it persists, warm the model (`ollama run gemma4:e2b`).
- **`model not found`** — `ollama pull` the model listed in the error.
- **Scan finds 0 jobs** — the configured company slugs might be wrong. Verify at `https://boards.greenhouse.io/<slug>` or `https://jobs.lever.co/<slug>`.
