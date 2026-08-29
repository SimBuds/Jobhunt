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
Write a short cover letter for {candidate_name} for one specific job. 3–4 paragraphs,
~250 words total. {candidate_name}'s voice: direct, concrete, no buzzwords, names real
projects and platforms.

Hard rules:
1. Use ONLY facts in `verified_facts` JSON. No invented projects, metrics,
   employers, or **technologies**. Cite specific project nouns drawn from
   `verified_facts.work_history` bullets OR from the `projects` array (the
   candidate's shipped personal projects) where they're relevant. When you anchor on a `projects` entry, use its
   exact `name` and only the tech in its `stack` or `bullets` — do not invent
   stack items or conflate it with a work-history project. Do **not** mention the degree or diploma, the
   school's name, academic honours (Dean's List and the like), coursework, or
   any other education item anywhere in the cover letter — that
   material lives in the resume's Education section and recapping it in the
   letter is auto-rejected. Do **not** claim familiarity with a tech the JD
   mentions but `verified_facts` does not (e.g. Elasticsearch, Kafka,
   Kubernetes, GraphQL, Vue, Angular, Django, Rails, Salesforce,
   ServiceNow, SAP). If the JD asks for one of those and {candidate_name} doesn't
   have it, **omit it** — do not bridge with "familiar with X". The
   deterministic validator rejects unverified tech claims.
2. Lead paragraph (2–3 sentences): open with a real first-person sentence
   that names the role + company AND a concrete hook from the JD — a
   product, a tech stack, a domain detail, something that shows {candidate_name}
   actually read the posting. **The literal company name MUST appear in
   paragraph 1 as a written-out token — not a pronoun ("you"), not a
   product name, not a description ("the AI platform"), the actual
   company name. Single-word company names (e.g. "Mercor", "Pigment",
   "Stripe") still appear verbatim. A deterministic validator rejects
   the letter if the company name is missing from the lead, and the
   retry budget is small — do not skip this.** NEVER start with
   "Applying for", "I am applying for", "I'm applying for", "I am
   writing to", "I am excited", "I'm excited", "I'm thrilled", or any
   other form-letter opener. Try leading with the **hook** (a concrete
   JD detail) rather than the act of applying. The second sentence
   should land the strongest specific match between {candidate_name}'s verified
   experience and the JD's must-haves.

   **AI/LLM lead-paragraph rule (May 2026).** If the JD explicitly
   mentions AI, LLM, RAG, generative AI, prompt engineering, ML,
   automation tooling, developer tooling, local-first tooling, or
   infrastructure work, the lead's hook MUST surface the candidate's own AI/LLM work as it appears in `verified_facts.skills_ai` and `verified_facts.projects`, naming the literal tool tokens found there (plus the generic tokens "AI" and "LLM") so an AI-screener summarizer pulls them. If `verified_facts` contains no AI/LLM work, skip this rule rather than inventing any. Do not treat vague
   phrases like "modern stack", "modern engineering", or "modern tools"
   as an AI trigger by themselves. For ordinary CMS, frontend, backend,
   or full-stack roles, lead with the strongest work-history project.
3. Middle paragraph (3–4 sentences): pick ONE project from verified_facts
   as the centerpiece and go deep — what the problem was, what shipped,
   what changed. The centerpiece may be a work-history project OR a
   `projects` entry. For AI, LLM, automation, infrastructure, or
   developer-tooling JDs, the `projects` entry whose stack best matches the
   JD's explicit requirements is often the strongest centerpiece. Go deep on
   what it does and what shipped. A second project may get one supporting sentence. Do
   NOT march through three projects in parallel ("At X… At Y… For Z…")
   — that reads like a CV recap, not a letter. Quantities (page counts,
   percentage improvements, catalog sizes) only if they appear verbatim in
   verified_facts.
4. If the JD lists a hard skill {candidate_name} is "Familiar" with rather than Core,
   **omit it from the letter entirely.** Do NOT introduce the gap
   defensively. Phrasings like "rather than <the missing tech>", "while I
   have... rather than", "the model transfers", "coming from X rather than Y" are
   FORBIDDEN — they volunteer weakness the reader did not ask about. Lead
   with what {candidate_name} *does* have for the role; let the resume show
   the rest. Do not pretend a Familiar skill is Core, and do not apologize
   for not having it.
5. Closing paragraph (1–2 sentences): forward-looking, not a re-recap of
   the resume. If the JD gives material about the company / product /
   mission, name what specifically draws {candidate_name} to *this* role. Otherwise
   a brief, plain offer to talk. Do NOT restate the degree or diploma,
   coursework, or skills here — those belong on the resume, not the
   letter's closing. Do NOT start the closing with "I am ready to" or
   "I'm ready to" — that's a formulaic gap-volunteering opener the
   validator rejects. Prefer concrete next-step phrasing that references
   a specific thing from earlier in the letter. Approved patterns:

   Each pattern below is a SHAPE: replace the angle-bracketed slot with a
   project, migration, or tool that appears in `verified_facts` and was
   already mentioned earlier in this letter. Never invent one.

   - "Happy to walk through the <project> build."
   - "Happy to step through the <migration> in more detail."
   - "Available to walk through the <system> design."
   - "Glad to step through how the <tool> work would apply here."
   - "Glad to step through the <tool> work if that is part of the role."
   - "Happy to dig into the <platform> side over a call."

   Each of these names a concrete artifact from earlier in the letter.
   Generic abstractions ("I'd welcome the chance to discuss how my
   experience aligns…") are rejected by the validator.
6. Voice: {candidate_name} writes like a person, not an HR template. Use
   contractions where natural ("I've", "I'm", "don't", "it's"). Vary
   sentence length — short punchy lines are fine. Direct, concrete, no
   buzzwords. Do not overstate adjacency. Avoid exact-fit claims,
   proof language, and one-to-one bridge phrases such as "translates
   directly", "directly mirrors", "this mirrors", or "perfect fit".
7. Banned phrases. The model has historically reached for these on every
   attempt — they are auto-rejected by a downstream validator, so using
   them wastes a retry. Read this list before you start writing, and
   before you submit. **NEVER write any of these, in any form:**

   - "aligns with"  ← recurring offender, do NOT use
   - "hit the ground running"  ← recurring offender, do NOT use
   - "I am ready to" / "I'm ready to"  ← formulaic closer, do NOT use
   - "ready to support" / "deliver immediately"  ← gap-volunteering
   - "passionate" / "deeply passionate"
   - "I believe"
   - "I'm excited" / "thrilled"
   - "leveraged" / "spearheaded"
   - "the model transfers" / "model transfers well"
   - "rather than" (when disclaiming a tech you don't have)

   Also banned (less common but still rejected): "synergy",
   "results-driven", "core requirements", "production-grade",
   "complementing my practical experience", "track record", "proven
   ability", "value-add", "direct match", "directly mirrors",
   "translates directly", "this mirrors", "mirrors the kind of",
   "technical rigor", "I'd bring to", "I'd welcome the chance", "the
   chance to discuss", "I'm drawn to", "transform enterprises", "support
   your team's goals", "coming from", "while I have".

   No first-person superlatives. No exclamation marks.

8. Anti-patterns (REWRITE if you catch yourself doing these):
   - Don't echo the company's marketing copy back at them ("your focus on
     performance-driven sales tech platforms"). Name what they do in
     plain language.
   - Don't pad with framing clauses ("The project required X, which
     mirrors Y"). Just say what you did.
   - Don't write a closing that's three abstract nouns in a row ("I'd
     welcome the chance to discuss how my hands-on experience with
     headless CMS architectures and performance optimization could
     support your team's goals"). One concrete, plain sentence.
   - Don't volunteer gaps. If the JD asks for X and {candidate_name} doesn't have X,
     say nothing about X. Never use "rather than", "while I have... rather
     than", "the model transfers", "coming from X rather than Y" in any
     form. Silence is stronger than apology.
   - Don't shoehorn the culinary background into IC engineering roles.
     The chef→tech bridge belongs ONLY when the JD genuinely calls for
     people-management, cross-functional coordination, vendor wrangling,
     or operational pressure as a JD-stated must-have. For a pure IC
     coding role, omit the culinary clause entirely — it's already on the
     resume. Phrasings like "my experience leading culinary teams... the
     model transfers" are forbidden.

9. Sentence rhythm: aim for an average of 15–18 words per sentence with
   real variance. Some sentences should be under 10 words. If three
   sentences in a row are over 25 words, rewrite.
10. Salutation: "Dear Hiring Team," unless the JD names a specific person.
11. Sign-off: set `sign_off` to "Best," followed by the candidate's full
    name exactly as it appears in the `name` field of the Verified facts
    JSON below. (The pipeline overwrites this field from the verified
    profile anyway, so never invent a name.) The `body` paragraphs MUST NOT
    contain a sign-off line — do **not** end the last paragraph with
    "Best,", "Regards,", "Sincerely,", "Cheers,", or the candidate's name.
    The sign-off is rendered separately; including it in `body` produces a
    duplicate sign-off and is rejected by the validator.

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
{revisions}

# Output format
Respond with a single JSON object using **exactly** these keys:
`salutation` (string), `body` (array of 3-4 paragraph strings), `sign_off`
(string). Do NOT use `paragraphs`, `content`, or any other key for the
body. Do NOT output markdown or prose. Begin your response with `{{`.
