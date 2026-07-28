# Tailoring rules (prompt-injectable)

Source of truth for the injected rules. Keep it short — this file is injected on
every tailor call and feeds the score prompt hash. Agent-facing companion (not
injected): `kb/policies/authoring.md`.

## Hard prohibitions (no fabrication)

- Use **only** facts present in `kb/profile/verified.json`. If a JD asks for something
  not there, name it as a gap — never invent.
- Do not invent metrics, employers, dates, projects, or skills.
- Do not promote a "Familiar" skill into a Core/primary category. The Familiar
  bucket is **exactly `verified_facts.skills_familiar`** — read it from there,
  never from memory. A skill absent from that list is not Familiar.
- Everything in that bucket is academic, coursework, or light-use only. Do not
  promote one to Core even if the JD asks. Name as gap if must-have.
- Conversely, a skill listed in any Core bucket **is** Core, even if it reads
  like a learning-stage technology. Judge by the bucket a skill appears in, not
  by the name. Python in particular is **Core (data/devops)** —
  {candidate_name} writes and operates this CLI daily.
- Do not fabricate a job title {candidate_name} has not held (no Senior/Staff/Lead/Principal/Architect).

## Reframe-only adjustments (allowed)

- Reorder skills inside categories so JD keywords appear first.
- Reword bullets to surface relevant verbs/nouns; same fact, different surface.
- **Use the JD's surface form** for known tech keywords. JD writes "Postgres" /
  "JS" / "GH Actions" / "Node" → the tailored bullet uses that exact form, not
  the verified.json long form. AI-screeners score on substring presence.
- **React umbrella.** Verified Core skill `React (Redux, React Native)` covers
  the React ecosystem. Render as just "React" by default; surface "Redux" or
  "React Native" as explicit items ONLY when the JD names them. They are
  Core-grade when surfaced — never a separate Redux item, never Familiar.
- **First skills-category matches the JD's primary stack.** Frontend role →
  lead with a category named for frontend; backend role → lead with backend;
  CMS → CMS; AI/LLM → AI tooling. `Familiar` bucket is always last.
- **Category sizes** (May 2026, Phase 8): first category 6–10 items,
  secondary categories 4–8 items, `Familiar` ≥4. Don't cram every Core/DevOps
  skill into the lead category — a 16-item lead reads like a keyword wall.
  Split across two or three categories when the JD spans multiple stacks.
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
- Required years exceed `years_experience + 3` with no transferable bridge.
- Title is Senior / Sr. / Lead / Staff / Principal / Architect AND
  `years_experience < 4`. Senior+ postings rarely waive YoE screens for
  sub-4-YoE candidates. When `years_experience >= 4`, these titles are
  valid IC roles — do not decline on the title alone.
- Title is people-management or non-IC (Manager, Senior Manager, Director,
  Head of, VP, including "Engineering Manager"). {candidate_name} is an IC engineer.
- Title is a non-engineering function (Sales, Partnerships, Account
  Executive/Manager, Customer Success, Marketing, Product/Project/Program
  Manager, Recruiter, Designer, Analyst, non-technical Consultant). Only
  hands-on coding roles qualify.
- Domain requires regulated experience {candidate_name} doesn't have (clinical, securities,
  medical devices).
- Not in Toronto/GTA + 100km and not Remote-Canada eligible.

## AI/LLM differentiator (§6, May 2026)

When the JD explicitly mentions AI, LLM, RAG, generative AI, prompt
engineering, ML, automation tooling, developer tooling, local-first tooling,
or infrastructure work:

- {candidate_name}'s baseline identity is full-stack JavaScript/TypeScript developer with
  CMS & e-commerce depth (July 2026 repositioning). For AI/LLM/automation-lane
  JDs, the AI automation tooling work (verified `skills_ai`) may headline the
  summary label — e.g. "developer building AI automation tooling" — but never
  an employment-title claim like "AI Engineer". For all other lanes AI tooling
  stays a supporting signal.
- The **resume summary**'s first or second sentence MUST surface {candidate_name}'s
  Ollama / local LLM / prompt-engineering work using the literal tokens
  "AI" and "LLM" for AI-adjacent roles so ATS keyword matchers latch on.
- At least one bullet in the most recent role MUST reference the AI/LLM
  tooling work concretely when the JD asks for it.
- The **cover-letter lead paragraph** MUST surface Ollama / local LLM /
  GPU-tuning in the hook sentence, not paragraph 3. AI-screener
  summarizers in 2026 pull the lead first. This is a strong differentiator
  for AI-adjacent roles and burying it costs the application.
- Do not treat vague phrases like "modern stack", "modern engineering", or
  "modern tools" as an AI trigger by themselves.
- For ordinary CMS, frontend, backend, or full-stack roles, lead with the
  strongest work-history project instead of forcing the local-LLM project.

## Tone guardrails (June 2026)

- Do not overstate adjacency between {candidate_name}'s projects and a JD.
- Avoid exact-fit claims, proof language, and one-to-one bridge phrases such
  as "translates directly", "directly mirrors", "this mirrors", or
  "perfect fit".
- Cover letters and form answers should use one centerpiece project by
  default. Add a second project only when the question or JD asks for breadth.
- Form answers must answer the exact question first. They are not mini cover
  letters.
