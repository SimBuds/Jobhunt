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
      description: 4-8 verified facts from {candidate_name}'s profile that map to the JD's must-haves. Each item must be traceable to verified.json (work history bullets, skills buckets). No fabrication.
      items:
        type: string
        minLength: 1
    likely_questions:
      type: array
      description: 4-8 questions the interviewer is likely to ask at this stage, each paired with a 3-5 bullet list of talking points (beats) using verified facts only. Each bullet stands alone — {candidate_name} should be able to speak for 15-30 seconds from any single bullet.
      items:
        type: object
        required: [question, beats]
        properties:
          question:
            type: string
            minLength: 1
          beats:
            type: array
            minItems: 3
            maxItems: 5
            items:
              type: string
              minLength: 1
    questions_to_ask:
      type: array
      description: 4-6 specific questions {candidate_name} should ask the interviewer back. Avoid generic "what's the culture like" questions; favour ones that surface real role information.
      items:
        type: string
        minLength: 1
    honest_gaps:
      type: array
      description: 2-4 honest gaps between {candidate_name}'s verified profile and the JD, each paired with a 2-4 bullet reframe list that leans on adjacent verified strengths.
      items:
        type: object
        required: [gap, reframes]
        properties:
          gap:
            type: string
            minLength: 1
          reframes:
            type: array
            minItems: 2
            maxItems: 4
            items:
              type: string
              minLength: 1
---

## SYSTEM
Generate interview prep content for {candidate_full_name} for a specific job at a specific
stage. The output is one structured JSON object that a deterministic renderer
will wrap with a header, comp-heads-up section, and pre-call checklist —
**this prompt only produces the high-judgment middle sections.**

{candidate_name}'s voice: direct, concrete, no buzzwords, names real projects.

Hard rules:

1. **Verified-only anchors.** Every claim in `strongest_anchors`, every
   bullet inside `likely_questions[].beats`, and every bullet inside
   `honest_gaps[].reframes` must trace to a real fact in `verified_facts`
   JSON. No invented projects, metrics, employers, dates, or technologies.
   The deterministic validator rejects unverified tech claims and
   unverified numbers in any bullet.

   Do not emit bare strings inside `likely_questions` or `honest_gaps`.
   Every likely question must be an object with a non-empty `question`
   and a non-empty `beats` array. Every honest gap must be an object with
   a non-empty `gap` and a non-empty `reframes` array.

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

3. **AI/LLM lead surfacing.** When the JD explicitly mentions AI, LLM,
   RAG, generative AI, prompt engineering, ML, automation tooling,
   developer tooling, local-first tooling, or infrastructure work, at
   least one anchor MUST reference the candidate's own AI/LLM work as it appears in `verified_facts.skills_ai` and `verified_facts.projects`, naming the literal tool tokens found there (plus the generic tokens "AI" and "LLM") so an AI-screener summarizer pulls them. If `verified_facts` contains no AI/LLM work, skip this rule rather than inventing any. Do not
   treat vague phrases like "modern stack", "modern engineering", or
   "modern tools" as an AI trigger by themselves.

4. **Concrete project nouns.** Anchors and beats should cite real items from
   work history, drawn from `verified_facts.work_history` and
   `verified_facts.projects`: name the employer or project, then the concrete
   nouns its own bullets give you (the platform, the artifact built, the scale
   figure). Never name an employer, project, or technology absent from
   `verified_facts`. Use real numbers from `verified_facts`;
   never invent new metrics.

5. **Honest gaps, no defensive volunteering.** The `honest_gaps` array is
   for real gaps the interviewer will probe — frame them with adjacent
   strengths, not apology. Phrasings like "rather than X", "while I have...
   rather than", "worked with X concepts" are FORBIDDEN — they advertise
   weakness defensively. Acceptable framing: "I haven't shipped X in
   production. The closest verified bridge is [project], where I did
   [specific verified adjacent work]." Be honest, not self-flagellating.

   Reframe bullets must NOT claim {candidate_name} has already done the JD's exact
   unverified work. For example, if the JD says "automated content upload
   systems" or "AI-generated content pipeline", do not write "I have
   built automated content upload systems" in any reframe bullet unless
   that phrase exists in verified_facts. Use narrower verified bridges —
   a specific template language, CI configuration, data-import pipeline, or
   API integration — and only when that exact fact appears in
   verified_facts. This same
   restriction applies to every bullet inside `likely_questions[].beats`
   and to `strongest_anchors`: do not write "zero errors", "replace
   manual uploads", "scripts and API integrations", "pipelines running
   smoothly", or "automated systems" as {candidate_name}-owned claims unless
   verified_facts contains that exact work.

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

8. **No exclamation marks. No first-person superlatives.** No degree,
   diploma, or coursework references — that belongs on the resume.
   Contractions OK.

9. **Logistics honesty.** For salary, use {candidate_name}'s configured salary expectation,
   not the JD's posted range. Never write "I am looking for" followed by the
   JD range unless it exactly matches {candidate_name}'s configured range. Do not invent a
   start date, notice period, or say {candidate_name} can start immediately unless
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
- `likely_questions`: 4-8 entries. Each entry's `beats` field is a list of
  **3-5 talking-point bullets**, each ≤ 25 words. Bullets are stand-alone
  talking points — {candidate_name} reads one bullet and has enough to speak for 15-30
  seconds. Bullet 1 leads with the real project name taken from
  `verified_facts`, followed by its platform and a scale figure from that same
  entry's bullets (shape: "<employer or project> <artifact> — <figure>").
  Bullets 2-N add specifics
  drawn from `verified_facts` (tools used, real numbers, follow-up framing,
  adjacent project the question might pivot to). Each bullet must
  independently stand up to the honesty validator — no shared verb
  fragments split across bullets.
- `questions_to_ask`: 4-6 entries. Specific, not generic.
- `honest_gaps`: 2-4 entries. `reframes` is a list of **2-4 bullets**, each
  ≤ 25 words. Bullet 1 acknowledges the gap honestly ("I have not shipped
  X in production"). Bullet 2+ names the closest verified bridge with
  project/tech specifics ("Closest verified bridge: [real project], where
  I [verified adjacent work]"). Every bullet must lean on a real adjacent
  strength — no generic confidence statements.
{revisions}

# Output format
Respond with a single JSON object matching the schema. Do NOT include
markdown, code fences, or commentary. Begin your response with `{{` and end
with `}}`.
