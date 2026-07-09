# IMPLEMENT.md: Execution engine

Pillar 4 of the documentation architecture defined in [AGENTS.md](AGENTS.md).
This file is the granular, phase-by-phase breakdown of in-flight work. Agents
**read** this at the start of every phase (Phase 1 and Phase 3 of the Workflow
Contract) and **write** to it as part of the Definition of Done, checking off
completed phases and logging deferred work as new phases. Never leave a plan in
conversational context alone. It lives here.

## Phase template

Copy this block for each phase of an approved plan. One phase = one atomic,
revertable commit. See Phase-Sizing Rules in `AGENTS.md`.

```
### Phase <N> - <one-sentence goal, no "and">

**Goal:** <single declarative sentence>

**Files to touch:**
- path/to/file - what changes

**Functions to add/change:**
- module.function - add | change - what it does

**Reuse audit:** (per Reuse-First Rule in AGENTS.md)
- Search terms: `<grep/rg terms used>`
- Candidates found: <list, or "none">
- Why not reused: <reason per candidate>

**Verification:** (<= 3 bullets)
- <how to prove it works, test name, or manual E2E output>

**Status:** [ ] not started | [ ] in progress | [ ] done
```

## Completed

### Phase R1 - Lane base resumes via `jobhunt resume` (2026-07-07)

**Goal:** Three regenerable lane-focused base resumes (AI Automation, CMS/E-com, Technical SEO) for manual channels, under the full-stack repositioning.

**Files touched:**
- kb/profile/verified.json + skills.md + resume.md + Baseline_Resume.docx - full-stack summary; new grounded skills "AI automation agents (Claude API, Ollama)" and "Technical SEO (on-page, Core Web Vitals, PageSpeed)" (round-trips through convert-resume byte-identically)
- kb/policies/tailoring-rules.md + kb/prompts/tailor.md rule 6a + Resume_Tailoring_Instructions.md + WORK.md - baseline identity is now full-stack with CMS/E-com depth; lane labels allowed when verified-grounded; Neurative AI real name in use
- kb/lanes/{ai-automation,cms-ecommerce,technical-seo}.md - new pseudo-JD briefs (bodies > thin_jd_chars)
- src/jobhunt/commands/resume_cmd.py + cli.py - new `jobhunt resume --focus ai|cms|seo|all`; synthetic Job per lane -> tailor_resume_with_retry -> render_docx; output data/resumes/
- tests/test_resume_cmd.py - brief parsing (real kb files), synthetic Job, CliRunner render with stubbed tailor

**Reuse audit:** tailor_resume_with_retry, render_docx.render, ensure_profile, apply_cmd contact-line pattern all reused; no new pipeline code.

**Verification:**
- pytest: 919 passed (8 new)
- `jobhunt resume --focus all` live: 3 DOCX rendered; SEO lane recovered clean on retry 2 (fabrication guard exercised)
- convert-resume round-trip after the docx edit: verified.json byte-identical

**Status:** [x] done

### Phase R2 - Two-page full-truth Baseline_Resume.docx (2026-07-07)

**Goal:** Expand the baseline into a parse-ready full-truth master so the tailor selects from richer verified facts (page count of the baseline is irrelevant; the one-page rule binds outputs only).

**Files touched:**
- Baseline_Resume.docx - 46 -> 53 paragraphs: split Dacko migration + Neurative CRM/launch bullets; 2-3 bullets per project (facts sourced from WORK.md Section 1); Core row restored to `React (Redux, React Native)` + Sass; CMS row gained Shopify App Development + Google Tag Manager (Stripe stays off, in-progress)
- kb/profile/* - regenerated via convert-resume; diff verified as additions-only
- Resume_Tailoring_Instructions.md §1 + §2, WORK.md - recorded the rework per the routing rule

**Reuse audit:** parse_docx already supports multi-bullet roles/projects; no code change.

**Verification:**
- convert-resume diff vs pre-change verified.json: intended additions only
- pytest: 919 passed
- `jobhunt resume --focus all` live: 3 lanes clean (SEO recovered on retry 2); portfolio resume.pdf refreshed at 1 page

**Status:** [x] done

