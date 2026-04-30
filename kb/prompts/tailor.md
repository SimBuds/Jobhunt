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
5. Education: include the GBC diploma line. Coursework: 4–6 items chosen from
   verified_facts.coursework_baseline OR the broader GBC list mentioned in
   `policy`, only items that map to the JD.
6. Summary: 3–5 sentences. Lead with what the JD wants. If the JD mentions
   AI/LLM/RAG/prompt engineering/ML/modern tooling, include the AI/Ollama line.
   Mention the GBC diploma + Dean's List once. No fabricated years.
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
