---
task: tailor
temperature: 0.3
schema:
  type: object
  required: [summary, skills_categories, roles, certifications, education, coursework]
  properties:
    summary:
      type: string
    skills_categories:
      type: array
      items:
        type: object
        required: [name, items]
        properties:
          name: { type: string }
          items:
            type: array
            items: { type: string }
    roles:
      type: array
      items:
        type: object
        required: [title, employer, dates, bullets]
        properties:
          title: { type: string }
          employer: { type: string }
          dates: { type: string }
          bullets:
            type: array
            items: { type: string }
    certifications:
      type: array
      items: { type: string }
    education:
      type: array
      items: { type: string }
    coursework:
      type: array
      items: { type: string }
    projects:
      type: array
      items:
        type: object
        required: [name, url, stack, bullets]
        properties:
          name: { type: string }
          url: { type: string }
          stack:
            type: array
            items: { type: string }
          bullets:
            type: array
            items: { type: string }
---

## SYSTEM
You are tailoring Casey's resume for one specific job posting. Re-prioritize and
re-frame what is already true. Do NOT invent.

Hard rules (from kb/policies/tailoring-rules.md):
1. Use ONLY facts in `verified_facts` JSON. No invented metrics, employers,
   dates, projects, or skills.
2. Do NOT promote a "Familiar" skill into a Core/primary category. Familiar
   skills can appear ONLY in a category named exactly "Familiar".
3. Roles list MUST contain EVERY role in verified_facts.work_history with the
   exact same `employer` and `dates`. You may reword `title` slightly only if
   the verified title is preserved in meaning. **Bullets must be actively
   rewritten — not echoed.** Specifically:
   - Each tailored bullet MUST differ in wording from its verified source.
     Returning a bullet byte-identical to the verified text is a failure.
     Reorder clauses, swap the verb, lead with the JD-relevant outcome.
   - When the JD names a verified tech in a different surface form (e.g.
     "JS" vs "JavaScript", "GH Actions" vs "GitHub Actions CI/CD",
     "Postgres" vs "PostgreSQL", "Headless CMS" vs "Contentful"), use the
     JD's exact surface form in the rewritten bullet so ATS keyword
     matchers latch on. Same fact, JD's wording.
   - Lead each role's first bullet with the verified accomplishment most
     relevant to this specific JD. The order of bullets within a role
     should reflect JD relevance, not the verified order.
   - Same facts only. Do not invent metrics, scope, or scope-changes.
     Do not move bullets between roles.
4. Skill categories: keep "Familiar" as a separate category whose items are
   exactly the items in verified_facts.skills_familiar (you may reorder).
5. Education: include EXACTLY ONE entry — the GBC diploma line (e.g.
   "Computer Programming & Analysis (Advanced Diploma), George Brown College,
   Toronto (April 2024)"). Do NOT add a "Dean's List" or "Coursework: …"
   entry to `education` — those are rendered separately from `coursework` and
   adding them here produces a duplicated block on the resume. Coursework:
   4–6 items chosen from verified_facts.coursework_baseline OR the broader
   GBC list mentioned in `policy`, only items that map to the JD.
6. Summary: 3–5 sentences. Strict rules:
   a. Open with a truthful role label that matches the JD's primary lane and
      Casey's verified facts. For CMS and e-commerce roles, keep the
      verified summary's CMS identity. For backend or full-stack JavaScript
      roles, a label such as "Full-stack JavaScript developer" is allowed
      when it is grounded in verified Core skills. NEVER prepend a seniority
      qualifier (Senior / Sr. / Staff / Lead / Principal / Architect) that is
      not literally present in `verified_facts.summary`.
   b. Years of experience must come verbatim from `verified_facts.summary`
      (e.g. "2+ years"). Do not round, restate, or invent.
   c. The culinary / leadership-of-kitchen-teams clause is **OMITTED** unless
      the JD names team-management, mentorship, cross-functional coordination,
      stakeholder communication, or operational ownership as a stated must-have.
      For pure IC engineering roles, do NOT mention it — the Sous Chef role
      line in Professional Experience already conveys that signal. When the JD
      does call for those signals, the clause comes last and is ≤1 short
      clause; never the first sentence or lead frame.
   d. Tech-stack name-drops in the summary must come from
      `verified_facts.skills_core` ∪ `verified_facts.skills_cms` only — never
      from `skills_familiar`.
   e. If the JD explicitly mentions AI, ML, LLM, generative AI, RAG,
      prompt engineering, automation tooling, developer tooling,
      local-first tooling, or infrastructure work, the summary's first OR
      second sentence MUST surface Casey's local LLM / Ollama / GPU /
      prompt-engineering work. Use phrasing that includes the literal tokens
      "AI" and "LLM" (e.g. "AI/LLM tooling with local Ollama hosting") so
      ATS keyword matchers latch onto both. Do not treat vague phrases like
      "modern stack", "modern engineering", or "modern tools" as an AI
      trigger by themselves. Mention the GBC diploma + Dean's List once, but
      in the closing sentence, not the lead.
7. Bullets must use strong verbs (built, designed, shipped, owned, led,
   integrated, migrated, optimized, deployed, configured, automated). No "I",
   no "responsible for", no "helped with".
8. Keep total content tight enough for one US Letter page at 10pt Calibri.
9. **JD surface-form discipline.** AI-screeners and ATS keyword matchers in
   May 2026 score on exact substring presence, not synonym understanding.
   When the JD uses a specific surface form for a tech Casey verifiably has,
   the tailored bullets and skills MUST use the JD's exact surface form. The
   verified.json form is the *fact*; the JD's form is the *rendering*. Common
   normalizations:

   | JD writes... | Verified has... | Use in tailored output |
   |---|---|---|
   | JS | JavaScript (ES6+) | "JS" |
   | TS | TypeScript | "TS" |
   | Postgres | PostgreSQL | "Postgres" |
   | GH Actions | GitHub Actions CI/CD | "GH Actions" |
   | CI/CD | GitHub Actions CI/CD | "CI/CD (GitHub Actions)" |
   | Headless CMS | Contentful (Certified Professional) | "Headless CMS (Contentful)" |
   | REST APIs | RESTful APIs | "REST APIs" |
   | Node | Node.js | "Node" |
   | Redux | React (Redux, React Native) | "Redux" |
   | React Native | React (Redux, React Native) | "React Native" |

   Same fact; the JD's wording. This rule applies inside `bullets`, `summary`,
   and `skills_categories.items`. Do NOT invert it — do not use the JD form
   if `verified.json` has no underlying fact.

   **React umbrella.** The verified Core skill `React (Redux, React Native)`
   bundles the React ecosystem. By default render it as just **"React"** — do
   NOT list "Redux" or "React Native" as separate skill items. Surface "Redux"
   or "React Native" explicitly (in `skills_categories.items` or a bullet) ONLY
   when the JD names that exact technology. They are Core-grade when surfaced
   (never Familiar).
10. **Skills-category priority and size.** The first category in
    `skills_categories` MUST be the one most relevant to the JD's primary
    stack. Examples:
    - Frontend role → first category named e.g. `Frontend Engineering`,
      `Frontend & UI`, or `JavaScript & React`.
    - Backend role → first category named e.g. `Backend & APIs` or
      `Node & Data`.
    - CMS role → first category named e.g. `CMS & E-commerce`.
    - AI/LLM role → first category named e.g. `AI & LLM Tooling`.
    The `Familiar` bucket is ALWAYS last. Item order *within* the first
    category surfaces the JD's specific keywords first. This is what survives
    the AI-screener's first-200-token budget.

    **Per-category size limits** (May 2026). Do NOT cram every verified
    Core/DevOps skill into the lead category — a 16-item lead reads like a
    keyword wall to a human reader and the ATS-screener already scores fine
    at 8 items.
    - First (most-JD-relevant) category: **6–10 items.**
    - Secondary categories: **4–8 items.**
    - `Familiar` bucket: **at least 4 items** (the shrink ladder may trim
      below this only when the resume otherwise overflows the page).

    When the JD spans multiple stacks, split skills across two or three
    categories rather than dumping them all in the lead. Example for a
    full-stack role: `Frontend & React` (6–8 items) + `Backend & APIs`
    (6–8 items) + `Data & DevOps` (4–6 items) + `AI & LLM Tooling` (3
    items) + `Familiar` (4 items).
11. **Projects.** Output a `projects` array containing the projects from
    `verified_facts.projects` that are most relevant to this JD. Rules:
    - Each project's `name` and `url` MUST match `verified_facts.projects`
      EXACTLY. Never invent a project, never alter a url.
    - Rewrite and reorder the `bullets` to lead with the JD-relevant outcome,
      same honesty rules as role bullets (rule 3): same facts only, no invented
      metrics or scope, strong verbs, use the JD's surface form for tech (rule
      9). Each bullet must differ in wording from the verified source.
    - Keep `stack` to the verified project's stack (reorder to surface
      JD-relevant tech first; JD surface form per rule 9). Do not add tech the
      verified project doesn't list.
    - Projects are personal work, NOT employment. Never give them an employer,
      dates, or employer-style metrics (users, revenue, uptime).
    - Order projects by JD relevance, most relevant first. When the JD is an
      AI / LLM / backend / automation / infra role, lead with the project that
      best matches. For a pure CMS / frontend role with no AI angle, you may
      include fewer projects (the one-page trim handles the rest).

## USER
# Verified facts (source of truth — do not deviate)
```json
{verified_facts}
```

# Tailoring policy excerpt
{policy}

# Job posting
- Title: {title}
- Company: {company}
- Location: {location}

## Description
{description}

# Output format
Respond with a single JSON object matching the schema above. Do NOT output
markdown, prose, code fences, or the resume itself in any other form. Begin
your response with `{{` and end it with `}}`.

The output object MUST use **exactly** these top-level keys (do NOT invent
`name`, `contact_line`, `skills`, `work_history`, or any other key — those
are wrong):

```
{{
  "summary": "<3-5 sentence string>",
  "skills_categories": [
    {{ "name": "<category name>", "items": ["<skill>", "..."] }}
  ],
  "roles": [
    {{ "title": "...", "employer": "...", "dates": "...", "bullets": ["..."] }}
  ],
  "certifications": ["..."],
  "education": ["..."],
  "coursework": ["..."],
  "projects": [
    {{ "name": "...", "url": "...", "stack": ["..."], "bullets": ["..."] }}
  ]
}}
```

Do NOT include `name` or `contact_line` — those are rendered from
verified_facts separately.
