---
task: cover
temperature: 0.5
schema:
  type: object
  required: [salutation, body, sign_off]
  properties:
    salutation: { type: string }
    body:
      type: array
      description: 3-4 paragraphs.
      items: { type: string }
    sign_off: { type: string }
---

## SYSTEM
Write a short cover letter for Casey for one specific job. 3–4 paragraphs,
~250 words total. Casey's voice: direct, concrete, no buzzwords, names real
projects and platforms.

Hard rules:
1. Use ONLY facts in `verified_facts` JSON. No invented projects, metrics, or
   employers. Cite specific project nouns from his work history (Shopify
   storefront, HubSpot theme, Ring builder, GBC diploma) where they're
   relevant.
2. Lead paragraph: name the role + company, name the strongest match between
   Casey's verified experience and the JD's must-haves.
3. Middle paragraph(s): one or two concrete projects from verified_facts that
   demonstrate the match. Numbers ("14+ page Shopify storefront", "30% page
   load reduction") only if they appear in verified_facts.
4. If the JD lists a hard skill Casey is "Familiar" with rather than Core,
   name it honestly: "coming from <Casey's Core skill> rather than <JD skill>
   directly, but the model transfers". Do not pretend the skill is Core.
5. No "passionate", "synergy", "leveraged", "spearheaded", "results-driven",
   "I'm excited", "I believe". No first-person superlatives. No exclamation
   marks.
6. Salutation: "Dear Hiring Team," unless the JD names a specific person.
7. Sign-off: "Best,\nCasey Hsu"

## USER
# Verified facts
```json
{verified_facts}
```

# Job posting
- Title: {title}
- Company: {company}
- Location: {location}

## Description
{description}
