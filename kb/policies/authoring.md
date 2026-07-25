# Resume authoring policy (agent-facing)

Workflow rules for a human or agent producing a tailored resume, cover note, or
recruiter reply **by hand** — outside the `jobhunt apply` pipeline.

**This file is deliberately NOT prompt-injected.** The pipeline loads exactly
one policy file, `kb/policies/tailoring-rules.md` (see `pipeline/tailor.py`,
`pipeline/score.py`). These are process rules an agent acts on, not constraints
the model needs in-context, and injecting them would spend tokens on rules the
model never applies.

**Facts always come from `kb/profile/verified.json`**, produced by
`jobhunt convert-resume` from the baseline resume. This file contains no
personal data by design — if you find yourself hard-coding a school, employer,
or project name here, it belongs in the profile instead.

**Companion files:**
- `kb/policies/tailoring-rules.md` — the hard prohibitions, ATS constraints,
  and auto-decline triggers. Prompt-injected. Read it first; this file does not
  repeat it.
- `kb/prompts/{score,tailor,cover,answer,interview-prep}.md` — the prompts
  themselves, each with JSON-schema frontmatter.

---

## 1. Inputs to demand before editing

1. **The full job description** — URL, paste, or file. Never tailor from a job
   title alone; generic tailoring against a title produces a worse result than
   the untouched baseline.
2. **The baseline resume**, located by `resume.locate.find_baseline_resume()`
   (any root-level `.docx` with "resume" in the name). It is a **full-truth
   master**, not a submittable document: it carries every verified fact so the
   tailor selects from the richest honest pool. The one-page rule binds
   *outputs* only.
3. Optionally, any experience the candidate has gained since the baseline was
   last edited — which should be added to the baseline, then re-parsed, not
   patched into a single output.

Lane base resumes for manual channels (LinkedIn, Indeed, recruiters) are
regenerable via `jobhunt resume --focus <lane>` from the briefs in `kb/lanes/`,
output to `data/resumes/`. Regenerate them whenever `verified.json` changes.

---

## 2. Tailoring workflow

Follow in order.

1. **Read the full JD.** Sort its asks into must-haves (years, primary stack,
   hard credentials), nice-to-haves (the "Preferred"/"Bonus" section — often
   where secondary strengths land), and domain context.

2. **Map every ask against `verified.json`**, marking each:
   - ✅ direct match (used in production)
   - ◐ adjacent match (transferable equivalent — see the peer-tech families in
     `kb/prompts/score.md`)
   - ▢ coursework only (learned academically, never used professionally)
   - ✗ gap

3. **Decide tailor vs. decline** before writing anything. Three or more
   must-have gaps means recommend skipping. See the auto-decline triggers in
   `tailoring-rules.md`.

4. **Rewrite the summary** (3–5 sentences): lead with what the JD actually
   wants rather than a generic identity line; include 2–3 must-have keywords
   the candidate genuinely has; never claim years of experience beyond
   `cfg.applicant.years_experience`.

5. **Re-order and re-categorize skills.**
   - The **first category must match the JD's primary stack** — it is what
     survives an AI screener's first-200-token budget.
   - Order keywords inside categories to put the JD's terms first.
   - The `Familiar` bucket is **always last** and is never promoted.
   - Drop categories that are clutter for the target role.
   - Sizes: lead category 6–10 items, secondary 4–8, `Familiar` ≥4. A 16-item
     lead reads as a keyword wall to a human, and ATS scoring saturates
     around 8.

6. **Re-emphasize bullets; never invent.**
   - Reorder within a role freely. Never move a bullet *between* roles.
   - Reword to surface a relevant verb or noun — same fact, different framing.
   - **Use the JD's surface form for tech keywords.** Screeners score on exact
     substring presence, not synonyms: if the JD says "Postgres", "GH Actions",
     or "Node", use those forms even when the profile stores "PostgreSQL",
     "GitHub Actions CI/CD", or "Node.js".
   - Split one dense bullet into two when both halves are relevant.
   - The current ("Present") role keeps its lead bullet **and** one supporting
     bullet at minimum; trim older roles first.
   - Strong verbs only: built, designed, shipped, owned, led, integrated,
     migrated, optimized, deployed, configured, automated. Never "responsible
     for", "helped with", "assisted in".

7. **Trim the coursework line** to the 4–6 entries matching the JD. Listing
   everything is noise.

8. **Page-fit check.** Output is exactly one page. If it spills, tighten or
   combine skill rows *before* cutting real content. If short, leave honest
   whitespace — do not pad.

9. **ATS check** against `tailoring-rules.md`.

10. **Deliver** per §5 below.

---

## 3. What may be adjusted

| Element | Allowed | Not allowed |
|---|---|---|
| Summary | Rewrite from scratch for the role | Claim seniority, years, or titles not held |
| Skill order | Re-prioritize freely; rename categories | Add unlisted skills; promote `Familiar` |
| Skill categories | Combine, split, rename | Invent a category holding skills not in the profile |
| Bullet wording | Reword for surface keywords; split dense bullets | Add responsibilities or invent metrics |
| Bullet order | Reorder within a role | Move bullets between roles |
| Coursework | Surface JD-relevant courses from the verified list | List courses not taken |
| Off-target roles | Shorten a role outside the target field to one line, or place it below education for senior postings | Delete real history; alter dates |
| Differentiator angle | Lead with it when the JD explicitly asks for that domain | Claim production systems that do not exist |
| Adjacent framing | Positive only — lead with what was actually done | "Rather than X", "coming from X", "the model transfers" — these volunteer weakness and the cover validator rejects them |

---

## 4. Pre-delivery pitfall audit

Check the output against every line before delivering.

1. **Silent `Familiar` promotion** — reverted?
2. **Invented metrics** — any "+20%" or team size not in the source? Remove.
3. **JD echo** — does a bullet just paraphrase the requirement back? Rewrite
   using the candidate's actual project specifics.
4. **Stack name-dropping** — did the skills section gain a tool from the JD's
   "preferred" list that the profile does not contain? Remove.
5. **Lost honesty signals** — was the `Familiar` category deleted to look
   stronger? Restore it.
6. **Two-page spill** — tighten skill rows before cutting content.
7. **Lost differentiator** when the JD's bonus section asks for it.
8. **LLM boilerplate** — "spearheaded synergistic solutions", "leveraged
   cutting-edge technologies". Rewrite with concrete nouns and verbs.
9. **Generic summary** — "passionate developer who loves solving problems"
   says nothing.
10. **Wrong company** in the filename or document body.
11. **Broken posting URL** — verify it resolves to the role described.
12. **Irrelevant coursework** left in.

---

## 5. Output and delivery

1. Emit a `.docx` via the project's renderer (`resume/render_docx.py`).
2. Validate the file before presenting it.
3. Render to PDF and inspect page 1 for one-page fit and clean formatting.
4. Summarize the **specific** changes made versus the baseline. Not "I
   tailored the resume" — rather "moved Python and Docker into the lead
   Languages row, rewrote the summary around REST APIs, surfaced the database
   and DevOps coursework, re-led the most recent role's first bullet with API
   integration".
5. Flag the honest gaps a recruiter will notice, and how to address them.
6. Offer a matching cover note or recruiter reply.

---

## 6. Cover notes and recruiter replies

The same honesty rules apply. Lead with the strongest genuine match; keep the
tone professional and human.

- **Differentiator lead rule.** When the JD explicitly names the candidate's
  differentiator domain, the lead paragraph must surface it using the literal
  token an AI screener would extract, not a paraphrase, and not buried in
  paragraph 3. Vague phrases like "modern stack" or "modern tooling" are not
  triggers by themselves.
- **Gap framing.** Naming a gap is fine *only when the JD explicitly asks for
  the missing tech*. Otherwise silence beats apology. "Rather than X", "the
  model transfers", and "coming from X" are forbidden — the deterministic
  validator rejects them.
- **Tone.** Do not overstate adjacency. Avoid exact-fit and proof language:
  "translates directly", "directly mirrors", "perfect fit". Use one centerpiece
  project by default; add a second only when the question asks for breadth.

---

*Extracted 2026-07-24 from a personal, untracked `Resume_Tailoring_Instructions.md`
so the generic policy survives in version control. Personal facts deliberately
excluded — they live in `kb/profile/verified.json`.*
