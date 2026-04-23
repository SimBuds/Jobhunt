# agent-instructions.md

## Goal

Transform `job-agent` into a streamlined AI-assisted personal job search operating system built for 2026 hiring best practices.

This project must be:

- human-in-the-loop
- legally compliant
- recruiter-safe
- ATS-optimized
- portfolio-quality
- practical for weekly personal use

This is NOT an auto-apply bot.

This IS a guided application system that helps discover, prioritize, tailor, and apply efficiently while keeping the user in full control of final submission.

---

# Required CLI Experience

```bash
job-agent scan
job-agent apply
job-agent digest
```

The workflow must be streamlined.

Do NOT separate scoring into a separate command.

`scan` must include automatic fit scoring.

---

# Non-Negotiable Rules

## MUST

- require manual final review before submission
- require manual final submit by user
- support ATS-safe resume tailoring
- maintain application logs
- maintain feedback loop for continuous improvement
- prioritize Toronto + Remote Canada developer roles
- be clean enough for public portfolio demonstration
- follow 2026 hiring best practices

## MUST NOT

- auto-submit applications
- bypass CAPTCHA
- violate platform Terms of Service
- perform credential abuse
- spam mass applications
- fabricate experience or qualifications
- use deceptive automation

---

# Command 1 — Scan

## Command

```bash
job-agent scan
```

## Purpose

Discover strong-fit jobs and automatically score them during the same run.

This should function as a weekly discovery + ranking engine.

No separate scoring command should exist.

---

## Sources

Prioritize:

- LinkedIn Jobs
- Indeed
- Greenhouse boards
- Lever boards
- startup company career pages
- Canadian startup job boards
- Toronto tech company career pages

Only use compliant browser-assisted discovery.

No aggressive scraping behavior.

---

## Role Filters

Focus on:

- software engineer
- software developer
- frontend engineer
- backend engineer
- full stack developer
- new grad roles
- internship roles
- junior developer roles
- early career engineering roles

Location priority:

- Toronto
- GTA
- Hybrid Toronto
- Remote Canada

---

## Automatic Scoring Requirements

Each discovered job must be scored immediately against the user’s resume.

### Scoring Factors

Evaluate:

- resume keyword match
- technical stack overlap
- project relevance
- internship relevance
- ATS keyword strength
- education relevance
- experience expectation alignment
- realistic interview viability

### Required Output

Example:

```text
92% fit — Shopify — Backend Intern
88% fit — Wealthsimple — SWE Intern
71% fit — Skip — Senior Staff Engineer
```

### Required Behavior

- high-fit roles prioritized first
- low-fit roles clearly marked to skip
- explain missing keywords
- explain why score was assigned
- recommend resume improvements when useful

---

## Output File

Store results in:

```text
applications/pipeline.json
```

## Required Schema

```json
{
  "company": "",
  "role": "",
  "url": "",
  "location": "",
  "salary": "",
  "tech_stack": [],
  "ats_platform": "",
  "date_discovered": "",
  "fit_score": 0,
  "priority": "high",
  "applied": false,
  "status": "new",
  "notes": ""
}
```

## Required Data Rules

- deduplicate jobs
- avoid repeated listings
- track stale listings
- support repeat weekly scans safely

---

# Command 2 — Apply

## Command

```bash
job-agent apply
```

## Purpose

Create a streamlined guided apply queue using best-fit unapplied roles first.

This should optimize quality over quantity.

---

## Required Flow

### Step 1

Select highest-priority unapplied job.

### Step 2

Generate ATS-safe tailored resume.

### Step 3

Generate tailored cover letter only if required.

### Step 4

Launch Playwright browser.

### Step 5

Autofill safe application fields only.

### Step 6

Pause for user review.

### Step 7

User manually clicks final submit.

### Step 8

After browser closes, ask:

1. Did you complete and submit the application? (yes/no)
2. Follow-up:
   - if yes → what adjustments were needed?
   - if no → what prevented submission?

### Step 9

Append feedback to:

```text
~/agent-instructions/feedback.md
```

---

## Required Safety

- never click final submit automatically
- never perform hidden submissions
- never attempt CAPTCHA bypass
- user must remain fully in control

This is mandatory.

---

# Command 3 — Digest

## Command

```bash
job-agent digest
```

## Purpose

Provide a simple CLI dashboard summary only.

Do NOT build email exports.

Do NOT build Notion exports.

Keep this streamlined.

---

## Example Output

```text
This Week

12 strong matches found
5 applications submitted
3 resume adjustments needed
2 jobs worth skipping
Top target: Shopify Backend Intern (92%)
Next target: Wealthsimple SWE Intern (88%)
```

This should be fast and useful.

No unnecessary reporting complexity.

---

# Resume Tailoring Rules

## Critical Requirement

Tailoring must optimize for ATS and recruiter clarity.

Not keyword stuffing.

---

## Required Best Practices

- preserve truthfulness
- reorder strongest bullets first
- emphasize measurable impact
- match employer terminology
- improve project relevance
- optimize recruiter readability
- keep formatting ATS-safe
- prioritize strongest developer projects
- align to 2026 hiring expectations

---

## Never Do

- fake experience
- fake metrics
- fake projects
- dishonest keyword stuffing
- unreadable AI-generated language
- low-quality generic cover letters

---

# 2026 Hiring Best Practices

The system must optimize for:

- focused high-quality applications
- fewer stronger applications over mass applying
- role relevance over quantity
- ATS keyword precision
- recruiter clarity
- measurable project impact
- visible ownership and product thinking
- strong technical project presentation

Avoid outdated “spray and pray” application behavior.

Quality-first is required.

---

# Portfolio Positioning

## Website Framing

Never describe this project as:

“AI auto applies to jobs”

Use:

## AI-Assisted Personal Job Search Operating System

This should demonstrate:

- responsible AI engineering
- workflow automation
- human-in-the-loop systems
- practical product design
- measurable hiring optimization
- strong engineering judgment

This framing is significantly stronger for recruiters.

---

# Recommended Technical Improvements

Prioritize:

- stronger structured logging
- config file support
- retry-safe browser flows
- failure recovery
- duplicate detection
- company blacklist support
- company priority list
- resume version tracking
- cleaner CLI UX
- application analytics

Future optional:

- interview tracking
- recruiter CRM
- referral tracking
- networking tracker
- recruiter follow-up reminders

Only after core workflow is excellent.

---

# Delivery Standard

This project should be strong enough that:

1. it is genuinely useful every week
2. it improves real hiring outcomes
3. it is strong enough for portfolio review
4. it demonstrates strong engineering thinking
5. it solves an actual painful workflow problem

Build toward that standard only.

