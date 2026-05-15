# Tailoring rules (prompt-injectable)

Source of truth: `Resume_Tailoring_Instructions.md` at the repo root. This file is
a trimmed, prompt-injectable mirror — keep it short. Update both when rules change.

## Hard prohibitions (no fabrication)

- Use **only** facts present in `kb/profile/verified.json`. If a JD asks for something
  not there, name it as a gap — never invent.
- Do not invent metrics, employers, dates, projects, or skills.
- Do not promote a "Familiar" skill into a Core/primary category. Casey's Familiar
  bucket is: Java, Spring Boot, MCP Servers, Agile/Scrum, Headless Architecture, Figma.
- Python is **Core (data/devops)**, not Familiar — Casey writes and operates this CLI
  daily. Do not list Python under Familiar.
- Java and Spring Boot are **Familiar** (GBC coursework only); do not promote to Core
  even if the JD asks. Name as gap if must-have.
- Do not fabricate a job title Casey has not held (no Senior/Staff/Lead/Principal/Architect).

## Reframe-only adjustments (allowed)

- Reorder skills inside categories so JD keywords appear first.
- Reword bullets to surface relevant verbs/nouns; same fact, different surface.
- **Use the JD's surface form** for known tech keywords. JD writes "Postgres" /
  "JS" / "GH Actions" / "Node" → the tailored bullet uses that exact form, not
  the verified.json long form. AI-screeners score on substring presence.
- **First skills-category matches the JD's primary stack.** Frontend role →
  lead with a category named for frontend; backend role → lead with backend;
  CMS → CMS; AI/LLM → AI tooling. `Familiar` bucket is always last.
- Split one dense bullet into two if both halves are relevant.
- Reorder bullets within a job. Never move bullets between jobs.
- The **Present** role (current contract) keeps ≥2 bullets — older roles
  shrink first when overflowing one page.
- Surface specific GBC courses (from `verified.json` coursework_baseline + the broader
  coursework list in §2) only when they map to JD requirements.

## ATS-safe output (§5)

- Single column. Calibri/Arial/Helvetica, 10–11pt body, 14–16pt name.
- Standard headings: Summary, Technical Skills, Professional Experience,
  Certifications & Education.
- Real list bullets (not typed `*` or `-`).
- One page. No tables-for-layout, no graphics, no header/footer text, no icons.
- No first-person pronouns. No "References available upon request." No Objective.

## Auto-decline triggers (§8)

If any of the following hold, mark the job `decline_reason` and skip:
- 3+ JD must-haves are gaps.
- Required years > 2x Casey's ~2.5–3 years of dev experience.
- Title implies Senior/Staff/Lead/Principal/Architect seniority.
- Title is people-management or non-IC (Manager, Senior Manager, Director,
  Head of, VP, including "Engineering Manager"). Casey is an IC engineer.
- Title is a non-engineering function (Sales, Partnerships, Account
  Executive/Manager, Customer Success, Marketing, Product/Project/Program
  Manager, Recruiter, Designer, Analyst, non-technical Consultant). Only
  hands-on coding roles qualify.
- Domain requires regulated experience Casey doesn't have (clinical, securities,
  medical devices).
- Not in Toronto/GTA + 100km and not Remote-Canada eligible.

## AI/LLM differentiator (§6, May 2026)

When the JD mentions AI, LLM, RAG, generative AI, prompt engineering, ML, or
"modern tooling" anywhere — even as a "nice to have":

- The **resume summary**'s first or second sentence MUST surface Casey's
  Ollama / local LLM / prompt-engineering work using the literal tokens
  "AI" and "LLM" so ATS keyword matchers latch on.
- At least one bullet in the most recent role MUST reference the AI/LLM
  tooling work concretely.
- The **cover-letter lead paragraph** MUST surface Ollama / local LLM /
  GPU-tuning in the hook sentence — not paragraph 3. AI-screener
  summarizers in 2026 pull the lead first; this is Casey's strongest
  differentiator and burying it costs the application.
