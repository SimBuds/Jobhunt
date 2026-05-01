---
task: cover
temperature: 0.7
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
2. Lead paragraph (2–3 sentences): open with a real first-person sentence
   that names the role + company AND a concrete hook from the JD — a
   product, a tech stack, a domain detail, something that shows Casey
   actually read the posting. NEVER start with "Applying for", "I am
   writing to", "I am excited", or any other form-letter opener. The
   second sentence should land the strongest specific match between
   Casey's verified experience and the JD's must-haves.
3. Middle paragraph (3–4 sentences): pick ONE project from verified_facts
   as the centerpiece and go deep — what the problem was, what shipped,
   what changed. A second project may get one supporting sentence. Do
   NOT march through three projects in parallel ("At X… At Y… For Z…")
   — that reads like a CV recap, not a letter. Numbers ("14+ page
   Shopify storefront", "30% page load reduction") only if they appear
   in verified_facts.
4. If the JD lists a hard skill Casey is "Familiar" with rather than Core,
   name it honestly: "coming from <Casey's Core skill> rather than <JD skill>
   directly, but the model transfers". Do not pretend the skill is Core.
5. Closing paragraph (1–2 sentences): forward-looking, not a re-recap of
   the resume. If the JD gives material about the company / product /
   mission, name what specifically draws Casey to *this* role. Otherwise
   a brief, plain offer to talk. Do NOT restate the GBC diploma,
   coursework, or skills here — those belong on the resume, not the
   letter's closing.
6. Voice: Casey writes like a person, not an HR template. Use
   contractions where natural ("I've", "I'm", "don't", "it's"). Vary
   sentence length — short punchy lines are fine. Direct, concrete, no
   buzzwords.
7. Banned phrases (do NOT use any of these): "passionate", "synergy",
   "leveraged", "spearheaded", "results-driven", "I'm excited", "I
   believe", "aligns with", "core requirements", "production-grade",
   "complementing my practical experience", "track record", "proven
   ability", "deeply passionate", "hit the ground running", "value-add".
   No first-person superlatives. No exclamation marks.
8. Salutation: "Dear Hiring Team," unless the JD names a specific person.
9. Sign-off: "Best,\nCasey Hsu"

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
