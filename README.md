# job-apply-agent

AI-powered CLI that scrapes a job posting, tailors your resume and cover letter with local Ollama models, renders PDFs, logs the application to SQLite, and optionally autofills the application form in a real browser.

Everything runs locally — no job data leaves your machine.

## Pipeline

1. **Scrape** — fetch + extract the job description with `cheerio` ([src/scrape.js](src/scrape.js)).
2. **Analyze** — extract structured JSON (`requirements`, `nice_to_haves`, `keywords`, `company_name`, `role_title`, `tone`) with `qwen2.5-coder:7b` ([src/analyze.js](src/analyze.js)).
3. **Tailor** — rewrite summary, reorder bullets toward the JD with `gemma4:e2b` ([src/tailor.js](src/tailor.js)). Never invents experience.
4. **Write** — draft a ~250-word cover letter with `gemma4:e2b` ([src/coverletter.js](src/coverletter.js)).
5. **Render** — produce `resume-{company}-{date}.pdf` and `coverletter-{company}-{date}.pdf` with `pdf-lib` ([src/render.js](src/render.js)).
6. **Track** — log the application to `data/applications.db` ([src/track.js](src/track.js)).
7. **Autofill** — open the posting in a real Chromium window and pre-fill contact fields for Greenhouse and Lever ([src/autofill.js](src/autofill.js)). Submit is left to you.

## Models

General-purpose models are used for writing; the coder model is reserved for structured JSON extraction.

| Model | Role | Used by |
|---|---|---|
| `qwen2.5-coder:7b` | Structured JSON extraction from JD | [src/analyze.js](src/analyze.js) |
| `gemma4:e2b` | Resume tailoring + cover letter prose (default) | [src/tailor.js](src/tailor.js), [src/coverletter.js](src/coverletter.js) |
| `qwen3.5:4b` | Fast fallback for the writing step (`--fast`) | [src/tailor.js](src/tailor.js), [src/coverletter.js](src/coverletter.js) |

All calls stream and are bounded by a 60s timeout. Ollama host is `http://127.0.0.1:11434`.

## Prerequisites

- **Node.js** 18+
- **Ollama** running on port 11434 with the required models pulled:
  ```bash
  ollama pull qwen2.5-coder:7b
  ollama pull gemma4:e2b
  ollama pull qwen3.5:4b   # optional, only needed for --fast
  ```
- **Playwright browsers** (only needed for autofill):
  ```bash
  npx playwright install chromium
  ```

## Setup

```bash
npm install
```

## Populate your resume

Edit [data/base-resume.json](data/base-resume.json) with your real information. The LLM reorders and rewords existing bullets — it does **not** invent experience, so the more detailed your base resume, the better the tailored output.

Expected top-level fields:

```json
{
  "name": "...",
  "email": "...",
  "phone": "...",
  "linkedin": "...",
  "github": "...",
  "website": "...",
  "location": "...",
  "summary": "...",
  "experience": [
    { "title": "...", "company": "...", "start": "...", "end": "...", "bullets": ["...", "..."] }
  ],
  "skills": ["..."],
  "education": [
    { "school": "...", "degree": "...", "start": "...", "end": "..." }
  ]
}
```

## Usage

### Apply to a job

```bash
node cli.js apply https://boards.greenhouse.io/company/jobs/12345
```

Flags:

- `--no-autofill` — skip the Playwright browser step (just produce PDFs and log the row).
- `--fast` — swap the writing model to `qwen3.5:4b` for quicker iteration.

Example:

```bash
node cli.js apply https://jobs.lever.co/acme/abc-123 --fast --no-autofill
```

### List applications

```bash
node cli.js list
```

### Update status

```bash
node cli.js status 1 interview
node cli.js status 1 rejected
node cli.js status 1 offer
```

## Supported platforms

Covers the ATSs used by most Toronto tech and finance employers in 2026:

| Platform | Scrape | Autofill | Examples |
|---|---|---|---|
| Greenhouse | Yes | Yes | Shopify, Cohere, Wealthsimple |
| Lever | Yes | Yes | Many startups |
| Workday | Yes | Best-effort | Scotiabank, RBC, TD, BMO, CIBC |
| Ashby | Yes | Yes | Newer YC / growth-stage startups |
| SmartRecruiters | Yes | Yes | Some enterprise |
| iCIMS | Yes | No | Legacy enterprise |
| LinkedIn | Yes | No | LinkedIn Jobs |
| Other career pages | Best-effort | No | Any |

**Workday autofill** is best-effort: the form spans multiple pages and often requires you to sign in first. The agent fills what's visible on the landing page; later pages may need manual entry.

**Dedupe**: re-running `apply` on the same URL updates the existing row instead of creating a duplicate.

## Output

- PDFs: `output/resume-{company}-{date}.pdf`, `output/coverletter-{company}-{date}.pdf`
- History: `data/applications.db` (SQLite) — schema: `applications(id, company, role, url, applied_at, status, resume_path, coverletter_path, notes)`

## Troubleshooting

- **`fetch failed` / `Fetch timed out`** — the target site blocked the request or was slow. Scrape has a 20s timeout. Try opening the URL in a browser and saving the JD text manually.
- **`Failed to extract JSON from tailor response`** — the writing model returned prose instead of JSON. Re-run; if it persists, confirm the model is pulled and Ollama is warm (`ollama run gemma4:e2b` once).
- **Autofill opens but fields stay empty** — the posting uses a custom form. Only Greenhouse and Lever are templated today.
- **`model not found`** — `ollama pull` the model listed in the error.
