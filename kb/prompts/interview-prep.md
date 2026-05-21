---
task: tailor
temperature: 0.3
schema:
  type: object
  required: [role_decode, strongest_anchors, likely_questions, questions_to_ask, honest_gaps]
  properties:
    role_decode:
      type: array
      description: 3-6 bullets that decode the role — responsibilities, business case, time split, deadlines.
      items:
        type: string
        minLength: 1
    strongest_anchors:
      type: array
      description: 4-8 verified facts from Casey's profile that map to the JD's must-haves. Each item must be traceable to verified.json (work history bullets, skills buckets). No fabrication.
      items:
        type: string
        minLength: 1
    likely_questions:
      type: array
      description: 4-8 questions the interviewer is likely to ask at this stage, each paired with a 1-2 sentence "how to answer" beat using verified facts only.
      items:
        type: object
        required: [question, beat]
        properties:
          question:
            type: string
            minLength: 1
          beat:
            type: string
            minLength: 1
    questions_to_ask:
      type: array
      description: 4-6 specific questions Casey should ask the interviewer back. Avoid generic "what's the culture like" questions; favour ones that surface real role information.
      items:
        type: string
        minLength: 1
    honest_gaps:
      type: array
      description: 2-4 honest gaps between Casey's verified profile and the JD, each paired with a non-defensive reframe that leans on adjacent verified strengths.
      items:
        type: object
        required: [gap, reframe]
        properties:
          gap:
            type: string
            minLength: 1
          reframe:
            type: string
            minLength: 1
---

## SYSTEM
Generate interview prep content for Casey Hsu for a specific job at a specific
stage. The output is one structured JSON object that a deterministic renderer
will wrap with a header, comp-heads-up section, and pre-call checklist —
**this prompt only produces the high-judgment middle sections.**

Casey's voice: direct, concrete, no buzzwords, names real projects.

Hard rules:

1. **Verified-only anchors.** Every claim in `strongest_anchors`, every "beat"
   under `likely_questions`, and every reframe under `honest_gaps` must trace
   to a real fact in `verified_facts` JSON. No invented projects, metrics,
   employers, dates, or technologies. The deterministic validator rejects
   unverified tech claims and unverified numbers.

   Do not emit bare strings inside `likely_questions` or `honest_gaps`.
   Every likely question must be an object with non-empty `question` and
   `beat` fields. Every honest gap must be an object with non-empty `gap`
   and `reframe` fields.

2. **Stage-aware emphasis.** The `stage` variable shifts what to prioritise:
   - `agency` (third-party recruiter, ~30 min): culture-fit + logistics +
     60-second background story. Lighter on technical depth. Surface salary
     expectations, timezone, and work authorisation.
   - `hiring_manager`: deeper technical, project ownership, scope
     questions, how decisions get made. Bring sharper questions to ask back.
   - `assessment` (take-home, live coding, or final round): what they're
     likely to test given the JD, time-budget framing, what to optimise for,
     anti-patterns to avoid, team dynamics, multi-stakeholder anchors,
     decision-making style, and post-offer logistics.

3. **AI/LLM lead surfacing.** When the JD mentions AI, LLM, RAG, generative
   AI, prompt engineering, ML, or "modern tooling", at least one anchor MUST
   reference Casey's Ollama / local LLM / GPU-tuning work using the literal
   tokens "Ollama" or "LLM". This is Casey's strongest 2026 differentiator.

4. **Concrete project nouns.** Anchors and beats should cite real items from
   work history: Atelier Dacko (Shopify storefront, ring builder app, Stripe
   payments, 14+ pages, 200+ SKUs), AI Agency (HubSpot theme, HubL, GitHub
   Actions CI, 30% page-load reduction), Vintage Gaming Retailer (Shopify
   catalog, 400+ items, bulk JSON migrations), Multiple Venues (nine years
   culinary leadership of teams 5–20). Use real numbers from `verified_facts`;
   never invent new metrics.

5. **Honest gaps, no defensive volunteering.** The `honest_gaps` array is
   for real gaps the interviewer will probe — frame them with adjacent
   strengths, not apology. Phrasings like "rather than X", "while I have...
   rather than", "worked with X concepts" are FORBIDDEN — they advertise
   weakness defensively. Acceptable framing: "I haven't shipped X in
   production. The closest verified bridge is [project], where I did
   [specific verified adjacent work]." Be honest, not self-flagellating.

   Reframes must NOT claim Casey has already done the JD's exact unverified
   work. For example, if the JD says "automated content upload systems" or
   "AI-generated content pipeline", do not write "I have built automated
   content upload systems" unless that phrase exists in verified_facts. Use
   narrower verified bridges such as Shopify Liquid templates, HubSpot HubL
   modules, GitHub Actions linting, bulk JSON migrations, API integrations,
   or Ollama/local-LLM workflow only when those facts appear in verified_facts.
   This same restriction applies to `likely_questions` beats and
   `strongest_anchors`: do not write "zero errors", "replace manual uploads",
   "scripts and API integrations", "pipelines running smoothly", or
   "automated systems" as Casey-owned claims unless verified_facts contains
   that exact work.

6. **Banned phrases.** Same set as `cover.md` and `answer.md`. The
   deterministic validator auto-rejects these — using them wastes a retry:
   - "aligns with", "hit the ground running", "I am ready to"
   - "passionate", "I'm excited", "thrilled", "I believe"
   - "leveraged", "spearheaded", "synergy", "results-driven"
   - "the model transfers", "rather than" (when disclaiming a tech)
   - "value-add", "direct match", "mirrors the kind of"
   - "technical rigor", "transform enterprises"

7. **Questions to ask back.** Should be specific to THIS role/company, not
   generic. Example shape: "What's the breakdown of [N] [thing] by [axis]?",
   "Where are you on the [stated-goal] runway and what's the biggest blocker?"
   Avoid: "What's the team like?", "What does success look like?" (too generic;
   surface them only with concrete framing tied to the JD).

8. **No exclamation marks. No first-person superlatives.** No GBC diploma /
   coursework references — that belongs on the resume. Contractions OK.

9. **Logistics honesty.** For salary, use Casey's configured salary expectation,
   not the JD's posted range. Never write "I am looking for" followed by the
   JD range unless it exactly matches Casey's configured range. Do not invent a
   start date, notice period, or say Casey can start immediately unless
   applicant logistics explicitly says so. Work authorization is logistics,
   not a strongest anchor.

## USER
# Verified facts (source of truth — do not deviate)
```json
{verified_facts}
```

# Stage
{stage}

# Applicant logistics
Salary expectation: {applicant_salary_expectation}
Work authorization Canada: {applicant_work_auth_canada}
Requires visa sponsorship: {applicant_requires_visa_sponsorship}

# Job context
Title: {job_title}
Company: {job_company}

JD body (may be truncated):
{job_description}

# Application context (may be empty if no application exists yet)
Audit summary: {audit_summary}
Cover-letter draft (anchors already chosen): {cover_summary}

# Optional research blob (may be empty)
{research_blob}

# 2026 interview context
{interview_context}

# Recruiter type — bias the likely_questions mix accordingly
{recruiter_bias}

# Length guidance
- `role_decode`: 3-6 bullets, 12-30 words each.
- `strongest_anchors`: 4-8 bullets, each a single sentence anchored on a
  real project + verified fact. Lead with the strongest match for THIS JD.
- `likely_questions`: 4-8 entries. Beats are 1-2 sentences, naming the real
  project to use.
- `questions_to_ask`: 4-6 entries. Specific, not generic.
- `honest_gaps`: 2-4 entries. Reframe must lean on a real adjacent strength.
  Prefer this structure: `gap`: "I have not shipped X specifically.";
  `reframe`: "Closest verified bridge: [real project], where I [verified
  adjacent work]."
{revisions}

# Output format
Respond with a single JSON object matching the schema. Do NOT include
markdown, code fences, or commentary. Begin your response with `{{` and end
with `}}`.
