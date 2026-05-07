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
   the verified title is preserved in meaning. Bullets must be reworded /
   reordered versions of the verified bullets — same facts, surfaced for the
   JD. Do not move bullets between roles.
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
   a. Open with the candidate's actual role label as it appears in
      `verified_facts.summary` (e.g. "Full-stack JavaScript developer"). NEVER
      prepend a seniority qualifier (Senior / Sr. / Staff / Lead / Principal /
      Architect) that is not literally present in `verified_facts.summary`.
   b. Years of experience must come verbatim from `verified_facts.summary`
      (e.g. "2+ years"). Do not round, restate, or invent.
   c. The culinary / leadership-of-kitchen-teams clause is OPTIONAL. If
      included, it must come last and be ≤1 short clause. It must NOT be the
      first sentence or the lead frame.
   d. Tech-stack name-drops in the summary must come from
      `verified_facts.skills_core` ∪ `verified_facts.skills_cms` only — never
      from `skills_familiar`.
   e. If the JD mentions AI, ML, LLM, generative AI, RAG, prompt engineering,
      or "modern tooling", the summary's first OR second sentence MUST surface
      Casey's local LLM / Ollama / GPU / prompt-engineering work — do NOT
      bury it as the closing sentence. Use phrasing that includes the literal
      tokens "AI" and "LLM" (e.g. "AI/LLM tooling with local Ollama
      hosting…") so ATS keyword matchers latch onto both. Mention the GBC
      diploma + Dean's List once, but in the closing sentence, not the lead.
7. Bullets must use strong verbs (built, designed, shipped, owned, led,
   integrated, migrated, optimized, deployed, configured, automated). No "I",
   no "responsible for", no "helped with".
8. Keep total content tight enough for one US Letter page at 10pt Calibri.

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
  "coursework": ["..."]
}}
```

Do NOT include `name` or `contact_line` — those are rendered from
verified_facts separately.
