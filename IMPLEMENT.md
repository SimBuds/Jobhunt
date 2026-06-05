# IMPLEMENT.md — Execution engine

Pillar 4 of the documentation architecture defined in [AGENTS.md](AGENTS.md).
This file is the granular, phase-by-phase breakdown of in-flight work. Agents
**read** this at the start of every phase (Phase 1 and Phase 3 of the Workflow
Contract) and **write** to it as part of the Definition of Done — checking off
completed phases and logging deferred work as new phases. Never leave a plan in
conversational context alone; it lives here.

## Current state

### Context-window bump to 32k (2026-06-04) — DONE

Raised the gateway-pinned `num_ctx` from 16384 to 32768 after measuring that the
new Ollama engine (0.30.3) keeps bare `qwen3.5:9b` Q4_K_M 100% GPU-resident at
32k (~5.6 GB on the 10 GB card). The `qwen3.5:9b-q8_0` build was evaluated and
rejected: ~10 GB weights spill to CPU at both 16k and 32k here, and the
head-to-head bench showed no quality gain over Q4_K_M (and a CUDA-500 crash mid
tailor).

- Changed: `gateway.client._DEFAULT_OPTIONS["num_ctx"]` 16384 to 32768, plus its
  comment block and docstring. `num_predict=4096` still bounds the in-band
  reasoning runaway regardless of `num_ctx`, so 32k does not reopen the hang.
- Test: `tests/test_gateway_errors.py::test_payload_pins_default_options`
  updated to assert 32768. Full suite green (806 passed). Live smoke bench
  (`bench_models.py --models qwen3.5:9b --runs 1`) at 32k: happy_fit ship 1/1 at
  100% coverage, decline_senior declined, fabrication_pressure fab-safe, 0
  errors. `ollama ps` confirmed CONTEXT 32768, 100% GPU.
- Docs synced: AGENTS.md (hardware context + LLM rules 3/4/5a), README.md
  (model + systemd blocks), PLAN.md (constraints + models tables). systemd env
  reconciled to live: KV cache q8_0, MAX_LOADED_MODELS=2, KEEP_ALIVE=-1,
  VULKAN=0, OLLAMA_CONTEXT_LENGTH removed (context is app-owned only).

### Context-budget exploitation initiative (2026-06-04) — proposed, awaiting approval

**Why:** The 32k bump gave ~28k input tokens (num_ctx − num_predict), but
several pre-LLM truncation caps were sized for the old 16k window. A DB sweep
(n=469 JDs: median 7,167 chars, p90 11,388, 63% > 6,000 chars, only 4% > 16,000)
shows interview-prep and answer starve on a 6k JD cap that clips the majority of
real JDs, while the scoring path already feeds up to 16k. This is an
inconsistency the headroom now lets us fix.

Diagnostic evidence (captured at plan time, do not re-derive):
- `pipeline.interview_prep._JD_MAX_CHARS = 6000` and
  `commands.answer_cmd.py:168 desc[:6000]` clip 63% of JDs. Score/tailor/cover
  already use `MAX_DESC_CHARS=16000` (clips 4%).
- `pipeline.interview_prep._RESEARCH_MAX_CHARS = 6000` and
  `commands.interview_prep_cmd.py:426 raw[:6000]` clip fetched company/JD pages
  in `--research` mode, where the content is additive (the strongest case for
  going beyond 16k).
- `MAX_POLICY_CHARS = 6000` is NOT a candidate: `tailoring-rules.md` is 4,898
  chars, under the cap. `MAX_DESC_CHARS = 16000` already covers 96% of JDs;
  raising it only helps outliers and invites boilerplate noise.

**Proposed phases (priority order, each its own approval):**
- P1 — DONE (2026-06-04): bound `interview_prep._JD_MAX_CHARS` and the
  `answer_cmd._load_jd_context` JD cap to the shared `MAX_DESC_CHARS` (16000)
  instead of a 6000 literal, so prep and answer see the same JD scope the
  score/tailor/cover path does. Changed: `pipeline/interview_prep.py` (import +
  constant + comment), `commands/answer_cmd.py` (import + truncation + comment).
  Test: new `tests/test_jd_context_caps.py` asserts `_JD_MAX_CHARS ==
  MAX_DESC_CHARS == 16000` and that a 10k-char JD survives `_load_jd_context`
  untruncated (both fail at the old 6000). Full suite green (809 passed). No
  ruff regressions (the 18 pre-existing E501/F401 are untouched, per
  no-piggybacking). `_RESEARCH_MAX_CHARS` deliberately left at 6000 for P2.
- P2 — DONE (2026-06-04): raised the `--research` caps so both fetched pages
  (JD URL + company root) survive into the prompt. `_RESEARCH_MAX_CHARS` 6000 to
  18000 (`pipeline/interview_prep.py`); the per-source `_strip_html` cap 6000 to
  a named `_RESEARCH_PER_SOURCE_CHARS = 9000` (`commands/interview_prep_cmd.py`),
  so 2 sources at 9000 fill the 18000 blob instead of the old cap dropping the
  second source. ~4.5k tokens, well under the 32k budget. Test: new
  `tests/test_research_caps.py` asserts the blob cap holds two sources and that
  `_strip_html` retains >6000 chars (fails at the old cap). Full suite green
  (811 passed); 0 ruff regressions on the two touched files (16 before, 16
  after).
- P3 — SKIPPED (2026-06-04, user decision): leaving `MAX_DESC_CHARS` at 16000.
  It already covers 96% of JDs; raising it to ~28000 helps only the 4% outliers
  and would invite more boilerplate noise into scoring. Revisit only if a real
  long-JD truncation problem surfaces.

**Initiative COMPLETE (2026-06-04): P1 + P2 done, P3 skipped.**

### Resume-parser format-robustness initiative (2026-06-02) — 5 phases, awaiting approval

**Why:** `resume.parse_docx.parse_baseline` is tightly coupled to Casey's
specific `Resume.docx` and fails silently when a resume diverges from that exact
shape. This blocks the stated goal of ingesting differently-formatted resumes
(the resume-parser-versatility goal). Three coupling points drop or misclassify
data with no feedback, the only signal today is the post-parse count line, and a
fourth point aborts the whole conversion on one bad line.

Diagnostic evidence (captured at plan time, do not re-derive):
- **Cert vs education classification is literal-string-coupled to Casey.**
  `parse_docx.py:238` keys on `"contentful certified"` and `"skill badge"`,
  `:240` on `startswith("Dean")` and `"Coursework"`. Any other resume's
  AWS / PMP / CompTIA cert silently lands in `education`, not `certifications`.
- **Skill-bucket labels are exact-match** (`:206-209`). A `Languages:` /
  `Frameworks:` / `Databases:` line matches `_SKILL_LINE_RE` but equals no
  bucket, so its skills fall through the loop and are dropped with no warning.
  PLAN.md already documents the sibling `skills_ai` run-on that needs a manual
  post-parse patch.
- **Section headers are exact-match** against the frozen `SECTION_HEADERS` set
  (`:184`). `WORK EXPERIENCE`, `EXPERIENCE`, or a bare `EDUCATION` produce an
  empty section silently.
- **Role parsing is fail-fast** (`:219` orphan bullet, `:223` unparseable
  header) while the PROJECTS loop (`:260-264`) skips bad lines gracefully. The
  two sections disagree on failure philosophy.

**Cross-phase inherited decisions (set at plan time, confirm during approval):**
- `parse_baseline` gains a warnings channel. Recommended shape: return a
  `(VerifiedFacts, list[str])` tuple. Blast radius is one production caller
  (`convert_resume_cmd.py:113`) plus the test file. **← confirm tuple vs a small
  `ParseResult` dataclass.**
- Unknown skill labels and ambiguous credential lines are WARNED and given a
  documented default placement. They are never silently dropped and never
  auto-invented into a new bucket. Casey relabels the docx if the default is
  wrong. **← confirm warn-and-default vs auto-create-bucket.**
- Role parsing switches from fail-fast to collect-and-report so a partially
  malformed experience section still yields the roles it can parse (matches the
  PROJECTS philosophy). **← confirm, or keep the hard raise.**
- This is parser-only work. No tailor / score / audit / render change, no
  `prompt_hash` impact, no config-schema change. Casey's own `Resume.docx` must
  parse identically before and after every phase (a regression guard, not a
  behavior change for him). The payoff is on OTHER resume shapes.

**Reuse audit (initiative-level, per Reuse-First Rule):**
- Search terms: `rg "parse_baseline"`,
  `rg "warn|logger|structlog" src/jobhunt/resume src/jobhunt/commands/convert_resume_cmd.py`,
  `rg "extract_certs|_KNOWN" src/jobhunt/analyze/certs.py`,
  `rg "_REGION_EXPANSIONS|PEER_FAMILIES|alias"`.
- Candidates found: `analyze.certs.extract_certs` / `extract_certs_split`
  (curated `_KNOWN` cert vocabulary plus generic `Certified X` / `X
  certification` patterns); `typer.echo(..., err=True)` (already the
  convert-resume error surface at `:142`); the `_REGION_EXPANSIONS` dict in
  `convert_resume_cmd` (an existing in-repo alias-map precedent).
- Why reused / not: the warning surface reuses `typer.echo(err=True)` (no new
  logging infra). The alias phases (RP3, RP4) reuse the `_REGION_EXPANSIONS`
  dict-lookup shape, adding only data. `extract_certs` is the cert-vocabulary
  candidate but is insufficient alone (no degree vocabulary, its `_KNOWN` omits
  Casey's `Contentful Certified` and `skill badge`, and it is tuned for JD
  prose, not terse credential lines), so RP2 proposes a small resume-tuned
  classifier rather than a resume to analyze cross-layer import. Reusing
  `extract_certs` is offered as an approval-time alternative. No existing
  warning-collection or header-normalizer exists in `resume/`, so those small
  helpers are genuinely new.

**Sequencing:** RP1 is the walking skeleton and must land first (it threads the
warnings channel end-to-end without changing what is parsed, so every later
phase has a place to report). RP2 through RP5 are independent of each other and
may be reordered or dropped during approval.

---

#### Phase RP1 — Surface parse warnings instead of silent drops

**Goal:** `parse_baseline` collects a warnings list for every line it cannot
classify and `convert-resume` prints it, with zero change to what gets parsed.

**Files to touch:**
- `src/jobhunt/resume/parse_docx.py` — `parse_baseline` accumulates a
  `warnings: list[str]` at each current silent-skip site (a skill line not
  matching `_SKILL_LINE_RE`, a skill label matching no bucket, an orphan
  PROJECTS bullet) and returns `(VerifiedFacts, warnings)`.
- `src/jobhunt/commands/convert_resume_cmd.py` — unpack the tuple at `:113`,
  then after the count summary print each warning via `typer.echo(..., err=True)`
  under a `parse warnings:` heading.
- `tests/test_parse_docx.py` — update the 3 `parse_baseline` call sites to
  unpack the tuple, and add `test_parse_warns_on_unknown_skill_label`.

**Functions to add/change:**
- `resume.parse_docx.parse_baseline` — change — new return type
  `(VerifiedFacts, list[str])`, accumulate warnings at the skip sites.
- `commands.convert_resume_cmd.run` — change — unpack and print warnings.

**Reuse audit:** the warning surface reuses `typer.echo(err=True)` (the existing
missing-fields error path at `convert_resume_cmd.py:142`). No new logging infra.
The only new construct is the in-function list.

**Verification (≤3 bullets):**
- Unit: a docx whose skills section has a `Languages:` line returns a non-empty
  warnings list naming that label, and the existing round-trip still passes with
  an empty warnings list for Casey's resume.
- `uv run pytest -q tests/test_parse_docx.py` green after the tuple-unpack update.
- Operational: `jobhunt convert-resume` on the real `Resume.docx` prints 0
  warnings (or only the known `skills_ai` run-on if present), proving no
  regression.

**Status:** [x] DONE (2026-06-02). `parse_baseline` now returns
`(VerifiedFacts, list[str])` and accumulates a warning at each former
silent-skip site: a TECHNICAL SKILLS line not in `Label: items` form, a skill
label matching no bucket (items reported, not dropped), and a PROJECTS bullet
before any project header. `convert_resume_cmd.run` unpacks the tuple and prints
a `parse warnings (N):` block to stderr after the count summary via
`typer.echo(err=True)`. Tests: updated the 2 returning `parse_baseline` call
sites to unpack (the missing-file case still raises inside `pytest.raises`),
added `test_parse_warns_on_unknown_skill_label` (a `Languages:` label warns and
its items reach no bucket), and added `assert warnings == []` to the round-trip
as a regression guard. Verification: `tests/test_parse_docx.py` 11 passed, full
suite **801 passed** (was 800 + 1 new), and `jobhunt convert-resume` on the real
`Resume.docx` printed 0 parse warnings (4 roles / 4 projects / 28 core / 6
project / 8 familiar, unchanged). Lint/types: 0 new ruff, 0 new mypy on touched
files; the 4 pre-existing convert_resume_cmd ruff errors (I001 + 3 E501 at
:3/:15/:144/:148) and the pre-existing parse_docx:129 untyped-`p` mypy note are
out of phase surface and left untouched. Docs: README `convert-resume` section
gained a one-line note on the `parse warnings` stderr behavior; PLAN.md not
touched (verified.json shape and the honesty model are unchanged, the warnings
channel is parser-internal observability).

---

#### Phase RP2 — Generic certification vs education classification

**Goal:** CERTIFICATIONS & EDUCATION lines split into cert vs education by
generic credential keywords rather than Casey-specific literals.

**Files to touch:**
- `src/jobhunt/resume/parse_docx.py` — replace the `:237-246` literal block with
  a `_classify_credential(line)` helper: degree keywords (bachelor, master,
  b.sc, b.a, phd, diploma, associate degree, university, college, honours) imply
  education; cert keywords (certified, certificate, certification, license,
  credential, badge) imply cert; an unclassifiable line defaults to education
  and emits an RP1 warning. The existing `Coursework:` extraction is preserved.
- `tests/test_parse_docx.py` — add `test_classifies_generic_certs`
  (AWS / PMP / CompTIA land in `certifications`) and `test_classifies_degrees`
  (a B.Sc line lands in `education`), and keep a Contentful / skill-badge case
  green.

**Functions to add/change:**
- `resume.parse_docx._classify_credential` — add.
- `resume.parse_docx.parse_baseline` — change — call the classifier in the
  CERTIFICATIONS & EDUCATION loop.

**Reuse audit:**
- Search terms: `rg "extract_certs|_KNOWN" src/jobhunt/analyze/certs.py`.
- Candidate: `analyze.certs.extract_certs` (curated `_KNOWN` plus generic
  `Certified X` / `X certification` patterns).
- Why a local classifier instead: `extract_certs` has no degree vocabulary, its
  `_KNOWN` omits Casey's `Contentful Certified` and `skill badge` (verified at
  plan time), and it is tuned for mining JD prose rather than terse resume
  credential lines. Reusing it would also couple `resume/` to `analyze/`.
  Reusing `extract_certs` as the named-pro-cert signal is offered as an
  approval-time alternative if the coupling is acceptable.

**Verification (≤3 bullets):**
- Unit: AWS / PMP / CompTIA lines classify as `certifications`, a
  `Bachelor of Science` line classifies as `education`, and Casey's
  `Contentful Certified` and `skill badge` still classify as `certifications`.
- Unit: an unclassifiable credential line lands in `education` AND emits an RP1
  warning.
- `uv run pytest -q` green.

**Status:** [x] DONE (2026-06-02). Replaced the Casey-specific literal block
(`startswith("contentful certified")` / `"skill badge"` / `startswith("Dean")`)
with `_classify_credential(text)` plus two module regexes: `_DEGREE_RE`
(bachelor / master / doctorate / phd / msc / bsc / beng / meng / ba / associate
degree / diploma / university / college / honours / dean's list / gpa / cum
laude) and `_CERT_RE` (certified / certificate / certification / licen[cs]e /
credential / badge). Degree vocabulary is checked first so a real degree wins,
and the "Associate" cert tier is excluded from the degree match (it requires
"associate degree") so an "AWS ... - Associate" cert is not mis-routed. The
`Coursework:` line is handled before the classifier (it carries no credential
keyword) and stays education. An unclassifiable line defaults to education and
emits an RP1 warning. Tests: `test_classifies_generic_certs` (AWS / PMP /
CompTIA / Contentful all land in `certifications`, the AWS Associate does not
leak to education) and `test_classifies_degrees` (Bachelor + Advanced Diploma
land in `education`). Verification: `tests/test_parse_docx.py` 13 passed, full
suite **803 passed** (was 801 + 2 new), and `jobhunt convert-resume` on the real
`Resume.docx` printed 0 warnings with certifications/education/coursework
identical to before (1 Contentful cert, 2 education lines, 4 coursework items).
Lint/types: ruff clean on touched files, 0 new mypy (the pre-existing
`parse_docx` untyped-`p` note shifted to :158 as the helper block was added
above it, untouched). Decision honored: local keyword classifier, no
`resume` to `analyze` import. Docs: this file. PLAN.md / README.md not touched
(no schema or user-facing-surface change beyond RP1's already-documented
warnings).

---

#### Phase RP3 — Skill-label aliasing plus unknown-label warning

**Goal:** Common alternate skill-section labels map onto the existing buckets,
and unrecognized labels warn instead of dropping their skills.

**Files to touch:**
- `src/jobhunt/resume/parse_docx.py` — add a `_SKILL_LABEL_ALIASES` dict
  (languages / frameworks / libraries to Core, databases / data / devops /
  infrastructure / cloud to Data & DevOps, cms / e-commerce to CMS & E-Commerce,
  ai / ml / tooling to AI & Tooling, project stack / projects to Project Stack,
  familiar / exposure to Familiar). Resolve the parsed label through the aliases
  before the exact-bucket match. On no match, emit an RP1 warning naming the
  label and its items, with no drop and no auto-bucket.
- `tests/test_parse_docx.py` — add `test_skill_label_aliases` (a `Frameworks:`
  line lands in `skills_core`) and `test_unknown_skill_label_warns`.

**Functions to add/change:**
- `resume.parse_docx._SKILL_LABEL_ALIASES` — add.
- `resume.parse_docx.parse_baseline` — change — alias-resolve the skill label.

**Reuse audit:**
- Search terms: `rg "_REGION_EXPANSIONS|alias|PEER_FAMILIES"`.
- Candidate: `_REGION_EXPANSIONS` (convert_resume_cmd) is the in-repo alias-map
  precedent (province abbreviation to full name). `PEER_FAMILIES`
  (pipeline._keywords) is tech-synonym data, not label aliasing.
- Why new data, not new mechanism: only a new alias dict is added; the
  dict-lookup pattern matches `_REGION_EXPANSIONS`.

**Verification (≤3 bullets):**
- Unit: a `Frameworks:` skills line populates `skills_core`, and a `Databases:`
  line populates `skills_data_devops`.
- Unit: a `Hobbies:` label warns (RP1) and is not silently dropped.
- `uv run pytest -q` green, and Casey's existing bucket labels still resolve
  unchanged.

**Status:** [x] DONE (2026-06-02). Added `_SKILL_LABEL_ALIASES` (lowercased
alternate label to canonical bucket: languages / programming languages /
frameworks / libraries / frontend to Core; databases / data / devops /
infrastructure / cloud to Data & DevOps; cms / e-commerce to CMS & E-Commerce;
ai / ml / tooling / tools to AI & Tooling; projects to Project Stack; exposure to
Familiar). In `parse_baseline` the skill-label resolution now tries the exact
bucket name first (so Casey's headings are unchanged) then the alias map, and
warns only when neither resolves. Resolution is inline (no new helper) to stay
within the planned surface. Tests: added `test_skill_label_aliases`
(`Frameworks:` to `skills_core`, `Databases:` to `skills_data_devops`, no
warnings); the RP1 `test_parse_warns_on_unknown_skill_label` was switched from
`Languages:` to `Hobbies:` because RP3 promoted `Languages` to a recognized Core
alias (this updated test now also serves the planned `test_unknown_skill_label_warns`
rather than adding a redundant duplicate). Verification: `tests/test_parse_docx.py`
14 passed, full suite **804 passed** (was 803 + 1 net new), and
`jobhunt convert-resume` printed 0 warnings with buckets unchanged (28 core / 6
project / 8 familiar). Lint/types: ruff clean on touched files, 0 new mypy (the
pre-existing untyped-`p` note shifted to :189 as the alias block was added above
it). Docs: this file. PLAN.md / README.md not touched (aliasing is parser
internal; the warnings surface is already documented from RP1).

---

#### Phase RP4 — Section-header aliasing

**Goal:** Alternate section-header spellings resolve to the canonical sections
so a non-Casey resume does not yield empty sections.

**Files to touch:**
- `src/jobhunt/resume/parse_docx.py` — add a `_SECTION_ALIASES` dict (work
  experience / experience / employment / work history to PROFESSIONAL
  EXPERIENCE, skills / technical skills / tech stack / technologies to TECHNICAL
  SKILLS, education / certifications / certs / licenses to CERTIFICATIONS &
  EDUCATION, projects / personal projects / selected projects to PROJECTS,
  summary / profile / objective / about to SUMMARY) and a
  `_canonical_section(text)` helper. Use the helper in both the `_first_section`
  detection and the section loop. Warn (RP1) if no recognized section header is
  found at all.
- `tests/test_parse_docx.py` — add `test_section_header_aliases` (a docx using
  `WORK EXPERIENCE` and `SKILLS` parses roles and skills non-empty).

**Functions to add/change:**
- `resume.parse_docx._SECTION_ALIASES` plus `resume.parse_docx._canonical_section`
  — add.
- `resume.parse_docx.parse_baseline` — change — use `_canonical_section` in both
  header checks.

**Reuse audit:** reuses the `_REGION_EXPANSIONS` dict-lookup precedent;
`_canonical_section` is a thin wrapper over the new dict. No existing header
normalizer exists.

**Verification (≤3 bullets):**
- Unit: a synthetic docx with `WORK EXPERIENCE` / `SKILLS` / `EDUCATION` headers
  parses roles, skills, and education non-empty.
- Unit: Casey's exact headers still resolve (round-trip unchanged).
- `uv run pytest -q` green.

**Status:** [x] DONE (2026-06-02). Added `_SECTION_ALIASES` (whole-line
lowercased alternate heading to canonical header: profile / professional summary
/ objective / about to SUMMARY; skills / tech stack / technologies / technical
proficiencies to TECHNICAL SKILLS; experience / work experience / employment /
employment history / work history to PROFESSIONAL EXPERIENCE; education /
certifications / certs / licenses (and the `& certifications` variants) to
CERTIFICATIONS & EDUCATION; personal / selected / side projects to PROJECTS) and
a `_canonical_section(text)` helper (exact case-insensitive match against
`SECTION_HEADERS` first, then the alias map). `parse_baseline` uses the helper in
both the `_first_section` contact-boundary detection and the section-collection
loop, and warns (RP1) when no recognized section header is found at all. Aliases
match the whole line, so a body line like "Experience with React" is not
mistaken for a header. Test: `test_section_header_aliases` (a docx using PROFILE
/ SKILLS / WORK EXPERIENCE / EDUCATION parses summary, skills, one role, and
education, with no warnings). Verification: `tests/test_parse_docx.py` 15 passed,
full suite **805 passed** (was 804 + 1 new), and `jobhunt convert-resume` printed
0 warnings with output unchanged (4 roles / 4 projects / 28 core / 6 project / 8
familiar). Lint/types: ruff clean on touched files, 0 new mypy (the pre-existing
untyped-`p` note shifted to :231 as the alias/helper block was added above it).
Docs: this file. PLAN.md / README.md not touched (section aliasing is parser
internal; the warnings surface is already documented from RP1).

---

#### Phase RP5 — Collect-and-report role parsing

**Goal:** An unparseable experience line is recorded as a warning instead of
aborting the whole conversion.

**Files to touch:**
- `src/jobhunt/resume/parse_docx.py` — in the PROFESSIONAL EXPERIENCE loop,
  replace the two `raise PipelineError(...)` calls (`:219` orphan bullet, `:223`
  unparseable header) with RP1 warnings. An orphan bullet before any role is
  warned and skipped, an unparseable non-bullet line is warned and skipped, and
  roles that parse are still collected. The empty-file guard at `:162` stays a
  hard raise.
- `tests/test_parse_docx.py` — add `test_orphan_bullet_warns_not_raises` and
  `test_unparseable_role_header_warns`, and keep a well-formed experience
  section green.

**Functions to add/change:**
- `resume.parse_docx.parse_baseline` — change — role loop warns instead of
  raising.

**Reuse audit:** reuses RP1's warnings list; the PROJECTS loop at `:260-264` is
the in-module precedent for lenient skip. No new mechanism.

**Verification (≤3 bullets):**
- Unit: a docx with an orphan bullet before any role header parses successfully
  with a warning (previously raised).
- Unit: a well-formed multi-role section parses all roles unchanged.
- `uv run pytest -q` green.

**Status:** [x] DONE (2026-06-02). Replaced the two `raise PipelineError(...)`
calls in the PROFESSIONAL EXPERIENCE loop (orphan bullet before any role header,
and a pipe-bearing line that does not match the role-header pattern) with RP1
warnings plus a skip, so a partially malformed experience section now yields the
roles it can parse instead of aborting the whole conversion (matches the
PROJECTS loop's lenient philosophy). The missing-file and empty-file guards stay
hard `PipelineError` raises, so `PipelineError` is still imported and used.
Tests: `test_orphan_bullet_warns_not_raises` (an orphan bullet is warned and
skipped, the following valid role still parses with its own bullet) and
`test_unparseable_role_header_warns` (a "Title | No Date" line is warned and
skipped, the valid role survives). Verification: `tests/test_parse_docx.py` 17
passed, full suite **807 passed** (was 805 + 2 new), and `jobhunt convert-resume`
printed 0 warnings with output unchanged (4 roles / 4 projects / 28 core / 6
project / 8 familiar). Lint/types: ruff clean on touched files, 0 new mypy (the
pre-existing untyped-`p` note unchanged at :231). Docs: this file. PLAN.md /
README.md not touched (failure-mode change is parser internal; the warnings
surface is already documented from RP1).

---

## Resume-parser format-robustness initiative — closing summary (2026-06-02)

Goal: let `parse_docx` ingest differently-formatted resumes instead of only
Casey's exact master. All five phases shipped, and Casey's own `Resume.docx`
parses byte-identically before and after (regression guard held at every phase:
4 roles / 4 projects / 28 core / 6 project / 8 familiar, 0 warnings).

- **RP1** threaded a warnings channel through `parse_baseline` (now returns
  `(VerifiedFacts, list[str])`) so every former silent drop is reported by
  `convert-resume` on stderr.
- **RP2** replaced the Casey-specific cert/education literals with a generic
  keyword classifier (degree-first, with the "Associate" cert tier guarded).
- **RP3** aliased alternate skill-section labels onto the canonical buckets and
  warns on truly-unknown labels rather than dropping their skills.
- **RP4** aliased alternate section headings (WORK EXPERIENCE, SKILLS, etc.) so
  a non-Casey resume no longer yields empty sections.
- **RP5** made role parsing collect-and-report instead of fail-fast, so one bad
  line no longer aborts the whole conversion.

Net: an unfamiliar resume now parses as much as it can and reports what it could
not classify, instead of silently dropping data or crashing. Suite ended at 807
passed. Parser-only: no tailor / score / audit / render / config / `prompt_hash`
change. Possible follow-ups (not scheduled): fix the `skills_ai` run-on that
PLAN.md still flags for manual patching, and add an `--strict` mode that exits
non-zero when warnings are present.

---

### SmartRecruiters empty-description fix (2026-06-01) — two phases, approved ("fix all")

**Why:** A `scan --skip-ingest` showed every `smartrecruiters:universityhealthnetwork`
posting warning "has no description to score". Confirmed in the live DB: 48/48
SmartRecruiters rows have an empty description while every other source has 0
empty. Root cause: the SmartRecruiters public **list** endpoint
(`/v1/companies/{slug}/postings`) returns only summary metadata, with no
`jobAd` field. The description lives on the per-posting **detail** endpoint
(`/v1/companies/{slug}/postings/{id}`), but `ingest/smartrecruiters.py`'s
`_extract_description` reads `jobAd.sections` off the list item, so it returns
`None` for every posting. The unit test passed only because
`tests/fixtures/smartrecruiters.json` hand-inlined `jobAd` on each list item, a
shape the real API never returns.

**User decisions (2026-06-01):**
- Fix path: fetch the detail endpoint per posting to populate the description
  (chosen over dropping UHN or pre-filtering before the fetch).
- Stale rows: re-ingest to backfill (a normal `scan` re-fetches + upserts the 48
  rows with descriptions); no manual DB surgery.
- Scope: "fix all" → also ship mitigations (a) pre-fetch non-eng gate AND
  (c) extend the non-eng filter with healthcare-clinical titles. Mitigation
  (b) "drop UHN" is EXCLUDED — it contradicts the keep-UHN/fetch-descriptions
  decision and discards UHN's few real eng roles (Full-Stack Developer, Backend
  Engineer, Software Developer, Cybersecurity). Surfaced for correction.

**Discovered cost this addresses:** UHN is a Toronto hospital, so nearly every
posting passes `is_gta_eligible`, and the detail fetch fires in the adapter
*before* `scan_cmd`'s ingest-time non-eng/senior title filters. Without
mitigations, a re-ingest would issue a 1-req/sec detail call for hundreds of
clinical roles that get filtered/declined anyway, and even with descriptions the
clinical roles (not in today's `_NON_ENG_TITLE_RE`) would flood the score queue.

**Phase ordering rationale:** (c) lands first as a self-contained filter
extension (helps every source, e.g. Workday hospital tenants, not just
SmartRecruiters). The core detail-fetch (Phase 2) ships WITH gate (a) already
referencing the extended filter, so the very first re-ingest after the work is
already efficient — there is no intermediate state that fetches hundreds of
clinical details.

---

#### Phase SR1 — Extend the non-eng filter with healthcare-clinical titles

**Goal:** `is_non_engineering_title` recognizes hospital clinical-role titles so
they are dropped at ingest across all sources.

**Files to touch:**
- `src/jobhunt/ingest/_filter.py` — extend the healthcare tier of
  `_NON_ENG_TITLE_RE` with a curated clinical group (personal support worker /
  PSW, respiratory + radiation therapist, occupational / physio / speech-language
  pathologist, social worker, dietitian, perfusionist, sonographer, paramedic,
  midwife, orderly, ward clerk, psychologist, optometrist, dental hygienist,
  medical lab technologist, ...). High-precision multi-word forms; `_ENG_GUARD_RE`
  still wins so a "Clinical Software Engineer" survives.
- `tests/test_non_eng_title_filter.py` — add clinical-title True cases + an
  eng-guard case (e.g. "Healthcare Software Engineer" → False).

**Functions to add/change:**
- `_filter._NON_ENG_TITLE_RE` — change — widen the healthcare alternation.

**Reuse audit:** reuses the existing `is_non_engineering_title` /
`_NON_ENG_TITLE_RE` / `_ENG_GUARD_RE` mechanism whole (G2). Only the regex
alternation grows; no new function, no new public interface.

**Verification (≤3 bullets):**
- New cases: the observed UHN clinical titles → `is_non_engineering_title` True;
  eng titles (incl. "Healthcare Software Engineer") → False.
- Live-DB check: run the extended regex over all `jobs.title` and confirm 0
  false positives among score ≥55 rows (mirror the G2 validation).
- `uv run pytest -q` green.

**Status:** [x] DONE (2026-06-01). Extended the healthcare tier of
`_NON_ENG_TITLE_RE` with a curated clinical group (personal support worker / PSW,
care attendant, porter, orderly, ward clerk, respiratory/radiation/occupational/
physio/physical therapist, speech-language pathologist, social worker, dietitian,
perfusionist, sonographer, paramedic, midwife, audiologist, optometrist,
kinesiologist, psychologist, dental hygienist/assistant, medical lab technologist,
pulmonary function, computed tomography, radiologic/MRI technologist). Added 19
test cases to `test_non_eng_title_filter.py` (15 clinical True + Healthcare
Software Engineer / Clinical Application Developer guard-wins + 3 real UHN
data/eng KEEPs). Live-DB validation: **25/46 distinct UHN titles dropped, 0 false
positives among score ≥55 rows** (Software Developer, Data Analyst, ML Specialist,
Cybersecurity, Bioinformatics all correctly kept). Suite 795 green; `_filter.py`
mypy clean, 0 new ruff (the pre-existing SIM103 in `is_gta_eligible` untouched).
AGENTS.md rule 9 non-eng bullet updated with the clinical tier.

---

#### Phase SR2 — Detail-fetch the description, gated by the non-eng filter

**Goal:** SmartRecruiters postings carry a real description by fetching the
per-posting detail endpoint, skipping the fetch for titles the non-eng filter
will drop.

**Files to touch:**
- `src/jobhunt/ingest/smartrecruiters.py` — add a `DETAIL_API` constant + a
  `_fetch_detail_description(client, limiter, slug, ext)` helper; add a
  `drop_non_eng: bool = True` keyword param to `fetch`; in the loop, when
  `_extract_description(j)` is `None` (always, live) AND not
  `(drop_non_eng and is_non_engineering_title(title))`, fetch the detail body and
  extract from it. Detail fetch wrapped so an `IngestError` degrades to
  `description=None` for that one posting (idempotent retry next scan), never
  aborting the slug. Always yield the row so `scan_cmd` stays the single
  drop authority + counter.
- `src/jobhunt/commands/scan_cmd.py` — pass
  `drop_non_eng=cfg.ingest.drop_non_engineering_titles` at the smartrecruiters
  call site (~line 377-380).
- `tests/fixtures/smartrecruiters.json` — strip `jobAd` from the list items so
  the fixture matches the real list shape (removes the bug-masking inline).
- `tests/fixtures/smartrecruiters_detail.json` — NEW: detail body for the
  Toronto posting (`jobAd.sections` with the TypeScript/Shopify text).
- `tests/test_ingest_adapters.py` — repoint `test_smartrecruiters_extract_description`
  at the detail fixture; add a `fetch`-level test (monkeypatch `get_json` to
  dispatch list-vs-detail by URL) asserting the Toronto Job gets a populated
  description, the Seattle posting triggers no detail fetch (GTA gate precedes),
  and a non-eng-titled Toronto posting triggers no detail fetch when
  `drop_non_eng=True`.

**Functions to add/change:**
- `ingest.smartrecruiters.fetch` — change — `drop_non_eng` param, conditional
  gated detail fetch, graceful per-posting failure.
- `ingest.smartrecruiters._fetch_detail_description` — add.
- `commands.scan_cmd._ingest_all` — change — one-line kwarg at the call site.
- `_extract_description`, `_format_location`, `_parse_dt` — unchanged (reused;
  `_extract_description` already reads the detail `jobAd.sections` shape).

**Reuse audit (Reuse-First Rule):**
- Search terms used: `rg "def get_json|def get_text" src/jobhunt/http.py`;
  `rg "content=true|jobAd|/postings/" src/jobhunt/ingest`;
  `rg "is_non_engineering_title|drop_non_eng" src/jobhunt`; `rg "_drain" tests/`.
- Candidates found: `http.get_json` (JSON GET + backoff + shared limiter);
  `greenhouse.fetch` (single-call `?content=true`); `_extract_description`
  (parses `jobAd.sections`); `is_non_engineering_title` (G2 filter, already
  imported in `scan_cmd`); the test-file `_drain` helper.
- Why reused / not: `get_json` reused verbatim for the detail call (same host →
  same limiter serialization). Greenhouse's one-call pattern can NOT be reused —
  no list-time expand param exists. `_extract_description` + `is_non_engineering_title`
  reused unchanged. Only new code: the detail URL helper + the gate condition.

**Verification (≤3 bullets):**
- `uv run pytest -q tests/test_ingest_adapters.py` — the new `fetch` test proves
  a populated description + the non-eng/GTA fetch gates; no network.
- `uv run pytest -q` — full suite green.
- Operational (re-ingest backfill): `jobhunt scan` then `SELECT COUNT(*) FROM
  jobs WHERE source='smartrecruiters' AND (description IS NULL OR
  TRIM(description)='')` → toward 0; report the count + wall-time.

**Status:** [x] DONE (2026-06-01). Shipped: corrected the `smartrecruiters.py`
docstring (list endpoint is summary-only, no `jobAd`); added `DETAIL_API` + a
`_fetch_detail_description` helper (`get_json` the `/postings/{id}` endpoint,
`_extract_description` it, swallow `IngestError` → None so one bad posting
degrades gracefully); `fetch` gained a `drop_non_eng: bool = True` keyword and a
gated conditional detail fetch (skip when `is_non_engineering_title(title)` and
the flag is on — rows are still yielded so `scan_cmd` stays the single drop
authority + counter); `scan_cmd` passes
`drop_non_eng=cfg.ingest.drop_non_engineering_titles` at the call site. Fixtures:
`smartrecruiters.json` stripped of `jobAd` (now matches the real list shape) +
a non-eng Toronto posting added; new `smartrecruiters_detail.json` detail body.
Tested: repointed `test_smartrecruiters_extract_description` at the detail
fixture; added `test_smartrecruiters_fetch_detail_and_non_eng_gate` (detail
fetched only for the eng role — not the GTA-filtered Seattle role, not the
non-eng Toronto role; non-eng row still yielded with description=None) and
`test_smartrecruiters_fetch_no_gate_when_drop_non_eng_false` (both Toronto roles
fetched with the gate off). Suite 797 green; both src files ruff + mypy clean
(the pre-existing `scan_cmd.py:585` Progress.update + the pre-existing test-file
E501/datetime lint were untouched). AGENTS.md ingestion rule 1 + the structure
comment updated with the detail-fetch behavior.

**Operational follow-up — re-ingest revealed a deeper bug (see Phase SR3).** The
user ran `jobhunt scan` to backfill, but all 48 UHN rows stayed empty. Root
cause: `db.upsert_job` was `INSERT OR IGNORE` (insert-only despite its name), so
the freshly-fetched descriptions were silently discarded for existing rows.
SR2's adapter code is correct (the detail endpoint was live-confirmed returning
`jobAd.sections`); the gap was the DB layer. Fixed in SR3.

---

#### Phase SR3 — Make `upsert_job` backfill descriptions + clear stuck UHN orphans

**Goal:** A re-ingest fills a missing description on an existing row, and the
pre-existing empty UHN rows are resolved.

**Why:** `db.upsert_job` used `INSERT OR IGNORE`, so re-ingest never updated
existing rows — "re-ingest to backfill" (the SR2 plan) could not work. Two
follow-on problems: (1) 20 eng/data UHN survivors had fetched-but-discarded
descriptions; (2) 28 UHN clinical roles were dropped at ingest by SR1's filter,
so they could never be refreshed AND never removed → permanent "no description
to score" warnings. User chose (2026-06-01) "fix upsert + delete clinical".

**Files touched:**
- `src/jobhunt/db.py` — `upsert_job`: after the `INSERT OR IGNORE`, when the row
  already existed AND this fetch carries a non-empty description, run a guarded
  `UPDATE jobs SET description=? WHERE id=? AND (description IS NULL OR
  TRIM(description)='')`. Backfills gaps only, never clobbers present data;
  return value still "True iff newly inserted" (preserves the "N new" count).
- `tests/test_db_writes.py` — `test_upsert_job_backfills_missing_description`
  (fails without the fix) + `test_upsert_job_does_not_clobber_existing_description`.
- `tests/test_parse_docx.py` — aligned the round-trip assertion to the "Jobhunt"
  project name (was the stale lowercase "jobhunt"; the docx project name was
  capitalized per the Jobhunt branding decision). Not caused by the db change;
  surfaced by the full-suite run.

**Operational (live DB, backed up to `data/jobhunt.db.bak`):**
- Deleted the 28 clinical UHN orphans (empty-description + `is_non_engineering_title`).
- Backfilled the 20 eng/data survivors' descriptions via the real detail-fetch
  path (`_fetch_detail_description` + the guarded UPDATE), confirming the
  end-to-end mechanism on live data.

**Reuse audit:** reuses the existing `upsert_job` SQL path (one guarded UPDATE
added); the operational backfill reuses `_fetch_detail_description` (SR2) +
`is_non_engineering_title` (SR1). No new public interface.

**Verification:**
- `uv run pytest -q` — full suite **799 passed**; the two new upsert tests fail
  pre-fix, pass post-fix. `db.py` ruff + mypy clean.
- Live DB: UHN rows went 48 → 20 (28 clinical deleted); **0 empty-description
  UHN rows remain** (20/20 survivors backfilled). The "no description to score"
  warnings are resolved.

**Status:** [x] DONE (2026-06-01).

---

### convert-resume: multi-paragraph contact block (2026-06-01) — DONE

**Why:** Casey manually reworked `Resume.docx` and wrapped the contact info onto
two paragraphs (links pushed to line 2). `parse_baseline` read only
`non_empty[1]` (the first contact paragraph), so `convert-resume` dropped
`https://caseyhsu.com` + `https://github.com/SimBuds` and kept a dangling
trailing `|`. Everything else parsed correctly (the Contentful cert is in
`certifications`, a separate field, not a drop).

**Fix:** `parse_baseline` now treats every paragraph between the name and the
first section header as the contact block and joins them (`"  "` separator),
so a 1-line or N-line contact both parse. The section loop already skipped
pre-`SUMMARY` paragraphs via its `current is None` guard, so no other change
was needed.

**Files touched:**
- `src/jobhunt/resume/parse_docx.py` — `parse_baseline` contact extraction
  (gather `non_empty[1:first_section]`, join).
- `tests/test_parse_docx.py` — new `test_contact_block_spans_multiple_paragraphs`
  (synthetic 2-paragraph docx, fails pre-fix); strengthened the round-trip
  contact assertion to require `github.com/SimBuds` + `caseyhsu.com`.

**Verification:**
- `uv run pytest -q` — full suite **800 passed**. `parse_docx.py` ruff clean
  (the line-129 untyped-param mypy note is pre-existing debt, untouched).
- `jobhunt convert-resume` regenerated verified.json + 5 sidecars; the
  `contact_line` now carries both URLs with no trailing `|`; 4 roles, 4
  projects ("Jobhunt"), Contentful cert, 28/6/8 skills all present.

**Status:** [x] DONE (2026-06-01).

---

### Projects-into-profile initiative (2026-06-01) — Approach B, awaiting approval

**Why:** Casey has four public, shipped AI / agentic projects (jobhunt,
Auto-Agent, SEO-LLM, AI Context Stack) that prove skills absent from every
verified skill bucket (FastAPI, Redis, Claude / Anthropic API, Docker Compose,
JSON-LD, agentic architecture) and that are a real hiring differentiator. Today
`scan` cannot credit a JD asking for those skills, the tailored resume cannot
show the projects, and `cover` cannot anchor on them. Goal: make the projects
first-class verified facts so they score, tailor onto the resume, and anchor
cover letters.

**Approach (user decision 2026-06-01, "curated master docx + PROJECTS"):**
Projects live in `Resume.docx` as new content, parsed by `parse_baseline` into
`verified.json` on every `convert-resume`. Casey keeps the master curated to
roughly one page of his strongest content. The tailor emphasizes and reorders
per JD (as it does for roles today); the one-page shrink ladder stays the safety
net. There is **no** separate projects file, **no** merge loader, and **no**
JD-aware selection engine (that larger "long master auto-trims per job" idea is
deferred as a possible future initiative, "Approach C").

**This SUPERSEDES the prior plan in this file** (separate `projects.toml` + a
`resume/profile.py` merge loader + a `prompt_hash` change). None of those are
needed now: projects flow through `verified.json`, which every consumer and
`prompt_hash` already read.

**Cross-phase inherited decisions (set at plan time):**
- Two distinct additions, kept separate:
  1. A **`skills_projects`** bucket: discrete project-proven skills, parsed from
     a new labeled line (`Project Stack: ...`) in the docx TECHNICAL SKILLS
     section via the EXISTING labeled-skills-line mechanism. Core-grade.
     Semantics, documented in AGENTS.md: "skills demonstrated in shipped
     personal projects. Honest to claim and creditable, distinct from
     `skills_familiar` (academic / light use) and the professional Core buckets
     (paid client work)."
  2. A **`projects`** narrative list: structured project entries (name, stack,
     bullets, optional url) parsed from a new `PROJECTS` docx section, rendered
     on the tailored resume like roles.
- Projects render like roles: every curated project in the master appears
  (rewritten / reordered per JD), NOT a JD-selected subset. Casey controls
  one-page fit by curating the master.
- Shrink-ladder order: a kept project keeps >= 1 bullet, and projects are
  trimmed AFTER coursework (the differentiator is trimmed late, not first).
- Operational: editing `Resume.docx` (adding the `Project Stack` line and the
  `PROJECTS` section) is Casey's step. The code only makes the parser, tailor,
  and renderer SUPPORT it. Each phase names the docx edit needed to exercise it.

**Reuse audit (initiative-level, per Reuse-First Rule):**
- Search terms: `skill_buckets`, `SECTION_HEADERS`, `_SKILL_LINE_RE`, `"skills_`,
  `_shrink_to_one_page`, `_enforce_no_fabrication`.
- Candidates: the existing labeled-skills-line parser (`_SKILL_LINE_RE` +
  `skill_buckets` dict) handles the `skills_projects` line with a one-entry dict
  change, no new mechanism; the section parser (`SECTION_HEADERS` loop) extends
  to `PROJECTS`; the shrink ladder extends with one rung.
- Why partial-new: a PROJECTS *narrative* is a section type the parser, tailor
  schema, and renderer do not model, so a small `Project` dataclass + render
  path are genuinely new. The skills bucket reuses the existing mechanism whole.

---

### Phase PB1 — Parse a `skills_projects` bucket and credit it in scoring

**Goal:** Scoring credits a project-backed skills bucket parsed from the master
resume.

**Files to touch:**
- `src/jobhunt/resume/parse_docx.py` — add a `Project Stack` label to the
  `skill_buckets` dict; add `skills_projects: list[str]` to `VerifiedFacts`;
  render it in `write_kb_markdown` (skills.md). `write_verified_json` carries it
  automatically via `asdict`.
- `src/jobhunt/pipeline/score.py` — add `"skills_projects"` to the
  `_all_matched_are_familiar` core-bucket tuple (~line 197) so a matched project
  skill counts as Core, not Familiar.
- `src/jobhunt/commands/convert_resume_cmd.py` — include `skills_projects` in
  the post-parse count summary line.
- `Resume.docx` (operational, Casey) — add `Project Stack: FastAPI, Redis,
  Claude API, Docker Compose, JSON-LD, agentic architecture` under TECHNICAL
  SKILLS; re-run `convert-resume`.

**Functions to add/change:**
- `resume.parse_docx.parse_baseline` — change — recognize the new skills label.
- `resume.parse_docx.VerifiedFacts` / `write_kb_markdown` — change — carry +
  render the bucket.
- `pipeline.score._all_matched_are_familiar` — change — treat bucket as Core.

**Reuse audit:** reuses the existing `_SKILL_LINE_RE` + `skill_buckets`
mechanism (one dict entry); no new parser surface.

**Verification:** (<= 3 bullets)
- Unit: a fixture skills section with a `Project Stack:` line populates
  `skills_projects`.
- Unit: `_all_matched_are_familiar(["FastAPI"], blob)` is False when FastAPI is
  in `skills_projects`.
- `pytest -q` green.

**Status:** [x] DONE (2026-06-01).
- Shipped: `VerifiedFacts.skills_projects` + a `Project Stack` entry in the
  `skill_buckets` dict (reuses the existing labeled-skills-line parser);
  `write_kb_markdown` renders a `## Project Stack` block in skills.md;
  `score._all_matched_are_familiar` core tuple now includes `skills_projects`
  so a matched project skill is Core-grade (not Familiar-capped);
  `convert_resume_cmd` summary reports the project-skill count.
- Tested: `test_parse_docx` asserts FastAPI lands in `skills_projects` (not
  core/familiar) and skills.md gets the heading; `test_score_clamp` adds
  `test_project_skill_counts_as_core_not_familiar`. Full suite 758 passed.
- Docs: this file + an AGENTS.md note on the `skills_projects` bucket.
- Lint/types: fixed the one new E501 (the widened core tuple). Pre-existing
  `score.py` SIM110 and `parse_docx.py:97` untyped-param are out of phase
  surface and left untouched.
- NOT done (correctly deferred): project skills do NOT yet render on the
  tailored resume (PB2) and the `PROJECTS` narrative is NOT yet parsed (PB3).
  The master `Resume.docx` already carries both the `Project Stack:` line and a
  `PROJECTS` section, so **do not run `convert-resume` until PB3** or the
  PROJECTS lines pollute `education`.

---

### Phase PB2 — Wire `skills_projects` through the tailor + audit honesty chain

**Goal:** Project skills can appear in the tailored skills section without
tripping fabrication enforcement or sinking audit coverage.

**Files to touch:**
- `src/jobhunt/pipeline/tailor.py` — add `"skills_projects"` to
  `_JD_SKILL_BUCKETS` (~294) and the fabrication-allowlist bucket tuple
  (~692-696); confirm `_ensure_jd_required_skills` backfills from it.
- `src/jobhunt/pipeline/audit.py` — add `"skills_projects"` to the skill-bucket
  iteration (~183).

**Functions to add/change:**
- `pipeline.tailor._enforce_no_fabrication` / `_JD_SKILL_BUCKETS` /
  `_ensure_jd_required_skills` — change — accept project skills as verified.
- `pipeline.audit` must-have extraction — change — include the new bucket.

**Verification:** (<= 3 bullets)
- Unit: a JD naming FastAPI yields a tailored skills list containing FastAPI and
  `_enforce_no_fabrication` accepts it.
- Unit: `audit.keyword_coverage` counts FastAPI as covered (no false `revise`).
- `pytest -q` green.

**Status:** [x] DONE (2026-06-01).
- Shipped: `"skills_projects"` added to `tailor._JD_SKILL_BUCKETS` (so a
  JD-named project skill backfills onto the resume), to the
  `_enforce_no_fabrication` allowlist tuple (so project skills are accepted, not
  flagged as invented), and to `audit._verified_skills` (so the deterministic
  must-have fallback + coverage credit them). No `apply_cmd` change needed:
  it already reads `verified.json` into the dict it passes to tailor/audit, and
  `skills_projects` will be present there once PB3 lands the parser.
- Tested: `test_tailor_backfill.test_backfills_jd_required_project_skill`,
  `test_tailor_invariants.test_accepts_project_skill_in_non_familiar_category`,
  `test_audit.test_extract_must_haves_includes_project_skill`. Full suite 761
  passed. Stash-compared lint: 0 new errors (15 pre-existing in the touched
  files left untouched, out of phase surface). Pre-existing mypy debt
  (`audit.py:230`) also left untouched.
- Docs: this file + the AGENTS.md `skills_projects` note (PB-status line).
- NOT done (deferred): the `PROJECTS` narrative section is still not parsed
  (PB3) and does not render on the resume (PB4). Project SKILLS now flow through
  the skills section, but project ENTRIES do not. `convert-resume` still must
  wait for PB3.

---

### Phase PB3 — Parse + carry the PROJECTS narrative section

**Prerequisite shipped early (2026-06-01):** Casey reformatted `Resume.docx` to
wrap role dates in parentheses (e.g. `(2023 – Present)`), which the role-header
regex rejected, so `parse_baseline` crashed on the first role. Per Casey's
choice ("extend the parser, keep parens"), `parse_docx._ROLE_LINE_RE` now
accepts an optional `(` before the month/year and keeps the parens in the
captured `dates` (so the tailored resume renders them back). `(NDA)` employer
suffixes still stay in the employer, and bare dates still parse. Covered by
`test_parse_docx.test_role_line_accepts_parenthesized_dates` and
`test_role_line_still_accepts_bare_dates`. Full suite 763 passed. This unblocked
the docx-parse confirmation but is NOT the narrative parsing below, which is
still pending approval.

**Goal:** `convert-resume` carries a structured projects list from a new
`PROJECTS` docx section.

**Files to touch:**
- `src/jobhunt/resume/parse_docx.py` — add `"PROJECTS"` to `SECTION_HEADERS`;
  add a `Project` dataclass (name, stack, bullets, optional url) +
  `projects: list[Project]` on `VerifiedFacts`; parse the section; render a new
  `kb/profile/projects.md`.
- `tests/test_parse_docx*.py` + a fixture — parser test for the PROJECTS block.

**Functions to add/change:**
- `resume.parse_docx.Project` — add.
- `resume.parse_docx.parse_baseline` — change — parse the PROJECTS section.
- `resume.parse_docx.write_kb_markdown` — change — write `projects.md`.

**Verification:** (<= 3 bullets)
- Unit: a fixture docx with a PROJECTS section parses into `projects[]` with
  name / stack / bullets.
- Unit: `verified.json` round-trips `projects`.
- `pytest -q` green.

**Status:** [x] DONE (2026-06-01).
- Shipped: `Project` dataclass (name, url, stack, bullets) + `projects` on
  `VerifiedFacts` (defaulted, so existing construction stays valid); `"PROJECTS"`
  added to `SECTION_HEADERS`; `_is_project_header` (header = a `Name | url` line
  whose right side is a whitespace-free URL token); the parse loop reads
  header/`Stack:`/bullet lines into structured projects; `write_kb_markdown`
  emits a 5th file `kb/profile/projects.md` when projects exist;
  `convert_resume_cmd` summary reports the project count. `asdict` round-trips the
  nested `Project` dataclass into `verified.json`.
- Also normalized the master `Resume.docx` (operational): consistent project
  blocks (`Name | url` / `Stack:` / bullets as separate paragraphs) and a clean
  single-line contact paragraph (the prior hyperlink fragments are gone). Backup
  at `Resume_prePB3_backup.docx`.
- Tested: `test_parse_docx` round-trip now asserts 4 structured projects (name /
  url / stack / bullets), no `education` leak, `verified.json` projects
  round-trip, and the 5th markdown file. Full suite 763 passed. 0 new lint
  (4 pre-existing convert_resume_cmd errors untouched); pre-existing parse_docx
  untyped-param left alone.
- **`convert-resume` is now SAFE to run.** The earlier "do not run until PB3"
  warning is lifted. Running it regenerates `verified.json` with `projects` +
  `skills_projects`, which the scorer (PB1) and cover (PB5) will use. The tailor
  will NOT yet render a PROJECTS section on the resume (that is PB4), so projects
  appear in scoring/cover context but not as resume entries until PB4.
- Ran `convert-resume` to confirm end-to-end (2026-06-01): 4 roles, 4 projects,
  6 project skills, parenthesized dates intact, no education leak. `verified.json`
  + `Resume.docx` are gitignored (local user state), not tracked changes.
- De-brittled `tests/test_audit.py::_minimal_tailored`: it hardcoded bare role
  dates, so once `verified.json` carried parenthesized dates the audit
  fabrication re-check saw a mismatch and 4 tests flipped to `block`. It now
  derives employer+dates from the active `verified` fixture (real or fallback),
  matching whatever date format is in use. Suite back to 763.

---

### Phase PB4 — Tailor renders a PROJECTS section within the one-page guarantee

**Goal:** Tailored resumes render a JD-emphasized Projects section that still
fits one page. SPLIT into PB4a (emit + validate) and PB4b (render + one-page
shrink) because the whole fails the one-sentence sizing test.

#### PB4a — Tailor emits + validates a `projects` output (no render) — DONE (2026-06-01)
- Shipped: `TailoredProject` dataclass + `TailoredResume.projects` (defaulted,
  last, so existing construction stays valid); `_parse` decodes the LLM
  `projects` array; `kb/prompts/tailor.md` gains the `projects` schema, rule 11
  (exact name+url, rewrite bullets per role-bullet honesty, JD-surface stack,
  never employment), and the output-example key; `_enforce_no_fabrication`
  rejects invented projects (`unverified-project`) and url tampering
  (`project-url-divergence`), with retry hints in `_violation_hint_line`. Name
  match is case-insensitive; missing verified projects are allowed (PB4b shrink
  may drop them).
- Tested: `test_tailor_invariants` adds accept-verified, case-insensitive,
  invented-project reject, url-tamper reject, decode, and missing-projects
  back-compat. Full suite 769 passed. mypy clean on tailor.py; 0 new lint.
- NOT done: projects still do not RENDER on the resume and have no shrink rung
  (PB4b). They are decoded + validated but `render_docx` ignores them.

#### PB4b — Render projects + one-page shrink rung — PENDING

**Files to touch:**
- `kb/prompts/tailor.md` — add `projects` to the output schema + a rule (include
  every verified project, rewrite / reorder bullets by JD relevance, no
  fabrication, mirror the role-bullet honesty rules).
- `src/jobhunt/pipeline/tailor.py` — decode `projects`; extend
  `_enforce_no_fabrication` to validate project name + bullets against verified
  projects; add a projects rung to `_shrink_to_one_page` (after coursework,
  keep >= 1 bullet per kept project).
- `src/jobhunt/resume/render_docx.py` — render the Projects section and account
  for it in `fits_one_page`.

**Functions to add/change:**
- `pipeline.tailor._tailor_once` / decode / `_enforce_no_fabrication` /
  `_shrink_to_one_page` — change.
- `resume.render_docx` — change — render projects + page-fit accounting.

**Verification:** (<= 3 bullets)
- Unit: `render_docx` page-fit passes with a projects section; the shrink rung
  drops a project's bullet (then a project) on overflow.
- Manual (real Ollama, not CI): `apply --url` on an AI-backend JD renders a
  one-page resume with a Projects section; audit ship/revise (not block).
- `pytest -q` green.

**Status:** PB4a [x] DONE (2026-06-01) · PB4b [x] DONE (2026-06-01).
- PB4b shipped: `render_docx` renders a PROJECTS section (bold name + right-tab
  url, a `Stack:` line, List-Bullet bullets) between PROFESSIONAL EXPERIENCE and
  CERTIFICATIONS & EDUCATION, only when projects exist; `estimate_lines` accounts
  for it so `fits_one_page` stays accurate; `_shrink_to_one_page` gains a
  projects rung AFTER coursework (drop trailing project bullets keeping >= 1
  each, then drop whole projects from the least-relevant end);
  `_tailored_resume_blob` (tailor) and `_resume_text` (audit) now include project
  name/stack/bullets so the JD-skill backfill dedup and keyword coverage stay
  consistent.
- Tested: render includes/omits PROJECTS, `estimate_lines` grows with projects,
  shrink rung trims bullets-then-projects keeping a lead. End-to-end smoke render
  confirms order Summary→Skills→Experience→Projects→Certs with all 4 real
  projects. Full suite 773 passed. mypy clean; 0 new lint (rule set identical
  before/after via stash diff).

---

### Phase PB5 — Cover letters can anchor on a verified project

**Goal:** The cover pipeline can anchor a paragraph on a verified project, and
the validator accepts named project skills.

**Files to touch:**
- `kb/prompts/cover.md` — additive rule permitting a verified-project anchor
  (keep existing anchor-honesty rules).
- `src/jobhunt/pipeline/cover_validate.py` — add `"skills_projects"` to
  `_verified_skill_blob` (~280) so a named project skill is not flagged as
  fabrication; confirm project names pass the anchor-authenticity check.

**Functions to add/change:**
- `pipeline.cover_validate._verified_skill_blob` — change — include the bucket.

**Verification:** (<= 3 bullets)
- Unit: `_verified_skill_blob` includes a `skills_projects` entry; a cover
  naming FastAPI does not trip the fabrication watchlist.
- Manual (real Ollama): a cover for an AI-backend JD references a project and
  passes `validate_cover`.
- `pytest -q` green.

**Status:** [x] DONE (2026-06-01). `_verified_skill_blob` now includes the
`skills_projects` bucket AND the `projects[]` narrative (name, stack, bullets)
so a cover anchoring on a verified project does not trip the fabrication
watchlist or overreach patterns. `cover.md` rule 1 + rule 3 were extended
additively to permit a `projects` entry as the centerpiece (jobhunt local-LLM
CLI called out as the strongest anchor for AI/LLM/backend JDs). `cover.py`
needed no loader change. Three tests added to `tests/test_cover_validate.py`:
blob includes projects, a project-stack tech (FastAPI) is suppressed, and the
suppression stays project-scoped (FastAPI absent from all sources still fires).
Suite 776 green, zero new ruff/mypy. **Scope note:** the plan listed only the
`skills_projects` bucket add; including the `projects[]` narrative was the
load-bearing addition for anchoring to actually work. Deferred to manual: a
real-Ollama cover for an AI-backend JD referencing a project.

### Projects initiative — final markdown sweep (2026-06-01)

**Status:** [x] DONE. Reconciled the human-facing docs against `verified.json`
(source of truth) after the date fixes and the PB1-PB5 feature work:
- `Resume_Tailoring_Instructions.md` Section 2 work-history table: AI Agency
  corrected to `Jan 2026 – Apr 2026` (was "2026 – Present"), Vintage Gaming to
  `Jan 2024 – May 2024` (was "2024"); the "two roles carry Present" note
  rewritten to reflect only the Custom Jewelry Brand contract is current;
  footer date bumped to June 1, 2026.
- `README.md` `convert-resume` sidecar list corrected to the real five files
  (`resume.md`, `skills.md`, `work-history.md`, `education.md`, `projects.md`).
- `PLAN.md` verified-snapshot section: added `skills_projects` to the bucket
  enumeration and a note that `verified.json` carries a `projects[]` narrative
  that renders on the resume, is audited, and can anchor a cover.
- `AGENTS.md` `skills_projects` note updated through PB5 (initiative complete).
The injected mirror `kb/policies/tailoring-rules.md` carries no date table, so
no stale facts there. Suite 776 green throughout. WORK.md project names/URLs and
GBC dates already matched `verified.json`. Em-dash/semicolon style cleanup is
forward-going per AGENTS.md and was not applied retroactively to PLAN.md /
Resume_Tailoring_Instructions.md (only edited prose was kept compliant).

---

### GTA-exposure initiative (2026-06-01) — 5 phases, awaiting approval

**Why:** A 3-day sample (153 jobs, 13 applications, 3 answers) showed the
apply/tailor/answer end is healthy (10 ship / 3 revise / 0 block; honest,
anchored answers) but the GTA-exposure funnel is starved upstream: the
applyable pool (score ≥55) is only 32 jobs and leans on Adzuna staffing-agency
reposts. Four leaks identified — dormant free sources, scan budget burned on
non-eng noise, junior roles wrongly declined as "Senior-band", and dead slugs.
User approved tackling all four (2026-06-01).

Diagnostic evidence (do not re-derive — captured at plan time):
- Sources: workday 76 · greenhouse 36 · adzuna 30 · manual 5 · ashby 2 · lever 2 · rss 2.
- `job_bank_ca=[]`, `workable=[]`, `recruitee=["synechron"→0 posts]` — adapters
  wired in `scan_cmd._ingest_all` (lines 376-381), starved of slugs/feeds.
- 17 configured slugs returned 0 posts/30d (`analyze employers --hiring-velocity`).
- Many sub-40 declines are non-eng Workday roles (Office Admin, Sanitation, Food
  Safety, Maintenance Tech, Legal, Account Executive) — no non-eng ingest filter
  exists (`_filter.py` has mgmt/research/senior only).
- Co-op/Campus/Intern titles match neither `is_senior_title` nor
  `is_explicit_junior_title`, so qwen's bogus "Senior-band" decline
  (`pipeline/score.py:108-113` override) is never nullified for them.

**Cross-phase inherited decisions (set at plan time):**
- New no-key sources that are *employer slugs* (Workable, Recruitee) go into the
  committed `kb/seeds/gta-employers.toml` + `config seed` loader (durable,
  reviewable). Job Bank feeds are *role-query RSS URLs*, not employers — they go
  in Casey's `config.toml` directly + a documented default set in README (not the
  employer seed file). **← surface for correction if you'd rather seed Job Bank too.**
- `drop_non_engineering_titles` defaults **True** (unlike `drop_research_titles`
  which is opt-in False) — non-eng functions are never a fit for any eng profile.
  **← confirm or flip to opt-in during approval.**
- Slug/feed population into Casey's live `~/.config/jobhunt/config.toml` is an
  operational step verified by a live scan; it is NOT a git commit (config.toml
  is not in the repo). Each phase's *committed* artifact is called out separately.

---

### Phase G1 — Rewrite Job Bank Canada adapter as a robots-respecting HTML scraper

**Original RSS plan BLOCKED (2026-06-01 live investigation):** Job Bank's public
RSS is dead — `format=rss` on `/jobsearch/jobsearch` returns the HTML page, and the
real feed `/jobsearch/feed/jobSearchRSSfeed?empl=...` returns an empty `<feed>`
(0 `<entry>`) even with a valid `jsessionid` + search context. Only the HTML results
page carries data. robots.txt = no Disallow, `Crawl-delay: 5`. **User approved (2026-06-01)
rewriting the adapter as an HTML scraper** — a sanctioned, documented exception to the
"public APIs only" rule (Job Bank is a Govt-of-Canada public service, robots-clean,
not in the forbidden-site list, and its sanctioned API is dead).

**Goal:** Replace the dead RSS adapter with an HTML-results parser that yields GTA-eligible Job Bank postings.

**Diff-surface note (justifies >5 surfaces):** core code is 3 files (adapter,
http primitive, scan wiring) + 1 test + 1 fixture; the AGENTS.md/README edits are
DoD doc-updates, not feature code. Splitting the rewrite from its wiring would ship a
half-wired adapter (not end-to-end, not cleanly revertable), so one phase is correct.

**Files to touch:**
- `src/jobhunt/ingest/job_bank_ca.py` — REWRITE: HTML `<article>` parser (stdlib `html.parser`), drop the RSS `_split_title` path.
- `src/jobhunt/http.py` — add `get_text(client, url, limiter, *, accept=...)` (backoff + limiter, returns `r.text`).
- `src/jobhunt/commands/scan_cmd.py` — pass a dedicated slow `RateLimiter(0.2)` (5 s) to the job_bank adapter to honor Crawl-delay.
- `tests/fixtures/job_bank_ca.html` — new captured results page; **delete** `tests/fixtures/job_bank_ca.xml`.
- `tests/test_ingest_adapters.py` — rewrite the job_bank case against the HTML fixture.
- `AGENTS.md` — amend ingestion rule 1 to record the Job Bank HTML-scrape carve-out + Crawl-delay handling.
- `README.md` — update the Job Bank config example (HTML search URLs, not RSS).
- `~/.config/jobhunt/config.toml` (operational) — set `[ingest] job_bank_ca = [<GTA search URLs>]`; verify scan.

**Functions to add/change:**
- `job_bank_ca.fetch` — change — fetch HTML via `get_text`, parse `<article>` results, bounded page walk, `is_gta_eligible` filter (precision gate; Job Bank location filter is loose — confirmed Thunder Bay leaked into a Toronto search).
- `job_bank_ca._parse_results` — add — stdlib `HTMLParser` subclass extracting per-article: posting id + url (strip `;jsessionid`), title (`span.noctitle`), employer (`li.business`), location (`li.location`), date (`li.date`), salary (`li.salary` → folded into description), remote hint (`span.telework`).
- `http.get_text` — add — text GET sibling of `get_json` (HTML Accept; reuses backoff + limiter).

**Reuse audit:**
- Search terms: `rg "def get_json|def fetch_feed|RateLimiter|strip_html" src/jobhunt`, `rg "HTMLParser|lxml|BeautifulSoup" src/`.
- Candidates found: `http.get_json` (JSON only), `_rss.fetch_feed` (sends `application/rss+xml` Accept → 406 on this HTML endpoint), `_rss.strip_html` (tag-strip), `RateLimiter` (per-host, global interval), lxml (transitive-only in uv.lock).
- Why not reused / how reused: `get_json` parses JSON (can't return HTML) and `fetch_feed`'s xml Accept 406s here → new `get_text` primitive; `strip_html` IS reused for the description; `RateLimiter` IS reused via a dedicated 0.2/s instance; lxml NOT promoted to a direct dep — stdlib `html.parser` keeps the no-new-dep convention (`_rss` precedent).

**Verification:** (≤3 bullets)
- New unit test: parse `job_bank_ca.html` fixture → asserts GTA rows kept, non-GTA (Thunder Bay) dropped, fields populated, `;jsessionid` stripped from URL.
- `pytest -q` green (old XML fixture/test removed cleanly).
- Operational: `jobhunt scan` → `SELECT COUNT(*) FROM jobs WHERE source='job_bank_ca'` > 0 with GTA locations.

**Status:** [x] DONE (2026-06-01). Rewrote `job_bank_ca.py` as a stdlib-`html.parser`-free
regex HTML scraper (no new dep); added `http.get_text`; wired a dedicated `RateLimiter(0.2)`
in `scan_cmd` for Crawl-delay: 5. New fixture `tests/fixtures/job_bank_ca.html` (+2 tests),
deleted the stale `.xml` + its 3 RSS tests. Suite 707 green, ruff/mypy clean on touched code
(net −1 pre-existing lint error). AGENTS.md rule 1/4 + structure comment + README config example
updated. Live E2E: `scan --skip-score` ingested 23 Job Bank postings → **14 net-new GTA rows**
(rest deduped vs Adzuna), all real Toronto/Mississauga employers (Nelson Education, Avant Techno,
Source Code, Upstaff, Intellgen, …). Config populated with 5 GTA role-query URLs.

---

### Phase G2 — Non-engineering ingest prefilter

**Goal:** Drop non-engineering-function titles at ingest so the scorer isn't burned on roles it will always decline.

**Files to touch:**
- `src/jobhunt/ingest/_filter.py` — add `_NON_ENG_TITLE_RE` + `is_non_engineering_title`.
- `src/jobhunt/config.py` — add `IngestConfig.drop_non_engineering_titles: bool = True`.
- `src/jobhunt/commands/scan_cmd.py` — wire into the drain-loop chokepoint + `filtered` dict + summary line.
- `tests/test_non_eng_title_filter.py` — new unit test.
- `AGENTS.md` — add the filter to the §"Ingestion rules" pre-score chokepoint list (item 9).

**Functions to add/change:**
- `_filter.is_non_engineering_title` — add — regex match for clearly non-eng functions (Administrator, Coordinator, Technician (non-software), Sanitation, Food Safety/FSQA, Buyer/Procurement/Supply, Production Supervisor, Account Executive/Sales, Legal/Counsel, Recruiter, Marketing, Accountant, Custodian). Word-boundaried; must NOT match Software/Data/QA/DevOps/Frontend/Backend/Full Stack engineer-dev titles.
- `scan_cmd._ingest_all` — change — add the drop branch + `filtered["non_eng"]` counter.

**Reuse audit:**
- Search terms: `rg "is_management_title|is_research_title|_RESEARCH_TITLE_RE" src/`.
- Candidates found: `is_management_title`, `is_research_title` (sibling title filters).
- Why not reused: they target different title classes (people-management; ML/research). Non-eng *functions* (admin/ops/sales/legal) are a disjoint set — overloading either regex would blur their documented contracts. New sibling matches the established pattern exactly.

**Verification:** (≤3 bullets)
- New test: non-eng titles (Office Administrator, Sanitation Associate, Food Safety Specialist, Account Executive) → True; eng titles (Software Developer, Full Stack Engineer, QA Contractor, DevOps Engineer) → False.
- `pytest -q` stays green.
- Manual: re-run `jobhunt scan --skip-ingest`? No — confirm via the new test + the scan summary line showing a non-eng drop count on next real scan.

**Status:** [x] DONE (2026-06-01). Added `is_non_engineering_title` + `_NON_ENG_TITLE_RE`
+ `_ENG_GUARD_RE` (guard wins) to `_filter.py`; `drop_non_engineering_titles=True` in
`config.py`; wired into the `scan_cmd` drain loop + `filtered["non_eng"]` + summary line.
New `tests/test_non_eng_title_filter.py` (32 cases). Suite 739 green; touched code ruff/mypy
clean (the SIM103 in `is_gta_eligible` + the 2 ApplicantProfile mypy errors are pre-existing,
untouched). Live-DB validation: 25/167 dropped, **0 false positives** among score ≥55. AGENTS.md
rule 9 updated. Default-True confirmed (flagged for flip in report; left on).

---

### Phase G3 — Junior/co-op title misfire fix

**Goal:** Extend the explicit-junior matcher so Co-op/Intern/Campus roles stop being declined as "Senior-band".

**Files to touch:**
- `src/jobhunt/ingest/_filter.py` — extend `_JUNIOR_TITLE_RE` with `co-?op`, `intern(ship)?`, `campus`, `early[-\s]talent`, `student`, `placement`.
- `tests/test_senior_title_filter.py` — add cases asserting these match `is_explicit_junior_title`.

**Functions to add/change:**
- `_filter._JUNIOR_TITLE_RE` — change — broaden the alternation.

**Reuse audit:**
- Search terms: `rg "is_explicit_junior_title|_JUNIOR_TITLE_RE" src/`.
- Candidates found: `is_explicit_junior_title` (the consumer in `pipeline/score.py:108`).
- Why not reused: this IS the function being extended — the score override already exists; it just needs the regex to recognize co-op/intern/campus markers.

**Verification:** (≤3 bullets)
- New cases: "Early Talent ... Co-op", "Software Engineer Intern", "Campus Recruitment Fall 2026" → `is_explicit_junior_title` True.
- `pytest -q` green; existing senior-title cases unchanged.

**Status:** [x] DONE (2026-06-01). Extended `_JUNIOR_TITLE_RE` with
`co-?op|intern(ship)?|campus|early-talent|student|practicum` (\b-bounded so
`internal`/`international`/`cooperative` don't false-match). Added an
`is_explicit_junior_title` parametrized test block to `test_senior_title_filter.py`.
Suite 757 green; `_filter.py` mypy clean. No AGENTS.md/README change — the junior-title
override (`pipeline.score`) is an existing documented mechanism; only its matcher widened.

---

### Phase G4 — Enable Workable + Recruitee via verified GTA slugs

**Goal:** Add live-verified Toronto Workable/Recruitee employer slugs to the committed seed pipeline.

**Files to touch:**
- `scripts/verify_seeds.py` — add Workable/Recruitee candidate probing (or run a one-off probe; pick the smaller path during execution).
- `src/jobhunt/commands/config_cmd.py` — extend `_SEEDABLE_ATSES` with `workable`, `recruitee`.
- `kb/seeds/gta-employers.toml` — add `workable = [...]` / `recruitee = [...]` (verified hits only).
- `README.md` — note the two new seedable ATSes.
- `~/.config/jobhunt/config.toml` (operational) — `config seed --apply` + verify scan.

**Functions to add/change:**
- `config_cmd._SEEDABLE_ATSES` — change — append the two ATS keys (loader/`seed`/`--preview` already iterate this tuple).

**Reuse audit:**
- Search terms: `rg "_SEEDABLE_ATSES|_load_seeds" src/`, `rg "is_gta_eligible" src/jobhunt/ingest/workable.py src/jobhunt/ingest/recruitee.py`.
- Candidates found: `config_cmd._load_seeds`/`seed` (tuple-driven, already generic over ATS keys); `ingest/workable.py` + `recruitee.py` (full adapters, GTA-filtered, wired).
- Why not reused: fully reused — only the `_SEEDABLE_ATSES` tuple + seed-file data need to grow; no new functions.

**Verification:** (≤3 bullets)
- Live probe each candidate slug; keep only those returning ≥1 GTA-eligible posting.
- `config seed --preview` lists the new workable/recruitee entries; `--apply` writes them.
- `jobhunt scan` shows `workable`/`recruitee` rows in `jobs`.

**Status:** [SKIPPED] (2026-06-01, user decision). Reconnaissance found ~0 discoverable
GTA Workable/Recruitee inventory and auto-discover already covers both ATSes at runtime,
so manual seeding adds no exposure now. No code shipped. Dead recruitee/synechron pruned
in G5. Reconnaissance below kept for the record.

**Reconnaissance (2026-06-01):**
Probed (a) 65 curated Canadian/GTA tech employers and (b) all 60 companies already
appearing in GTA scans (via the discover normalizer) against both Workable + Recruitee
through the real GTA-filtering adapters. Result: a handful of real accounts (ada,
wealthsimple, apollo) but **0 with any GTA-eligible postings**; unknown slugs 404
cleanly. Auto-discover ALREADY probes Workable/Recruitee on every scan, so the runtime
path is covered — the only gap the planned plumbing fills is cold-start `config seed`
import of *manually-curated* slugs, of which there are currently none to add. Net GTA
exposure from G4 right now ≈ 0. Options: (A) ship seed-loader plumbing only (extend
`_SEEDABLE_ATSES`, README) for future hits, no slugs; (B) skip G4, rely on auto-discovery,
go to G5; (C) wide WebSearch-driven slug hunt (high effort, uncertain).

---

### Phase G5 — Prune dead slugs + add verified GTA Greenhouse/Lever/Ashby employers

**Goal:** Remove the 17 zero-post slugs and seed freshly-verified GTA employer boards.

**Files to touch:**
- `~/.config/jobhunt/config.toml` (operational) — `jobhunt config reprobe --prune`.
- `scripts/verify_seeds.py` — add new GTA Greenhouse/Lever/Ashby candidates to vet.
- `kb/seeds/gta-employers.toml` — add the verified subset.

**Functions to add/change:** none — uses existing `config reprobe` + `verify_seeds.py` + `config seed`.

**Reuse audit:**
- Search terms: `rg "reprobe|_PROBEABLE_ATSES" src/jobhunt/commands/config_cmd.py`.
- Candidates found: `config_cmd` reprobe path (probes + prunes stale slugs); `verify_seeds.py` (curation-time verifier).
- Why not reused: fully reused — this phase is operation + curated data only.

**Verification:** (≤3 bullets)
- `config reprobe` prints live vs stale; `--prune` removes the 17 (with confirmation).
- `verify_seeds.py` output confirms each new slug is live before it enters the seed file.
- `jobhunt scan` post-change shows net-higher real-employer GTA volume.

**Status:** [x] DONE (2026-06-01). **Prune corrected from plan:** `config reprobe`
(read-only first) showed only `lever/benchsci` is genuinely stale (404) — the 17
"zero-post" slugs from hiring-velocity are LIVE boards with no GTA posts in-window
(a different metric); pruning them would cut future exposure, so they were KEPT.
recruitee/synechron is live (3 non-GTA offers) → kept. Removed only benchsci (config
+ seed + verify_seeds). **Add:** probed a curated GTA-employer candidate set against
Greenhouse/Lever/Ashby/SmartRecruiters via the real GTA-filtering adapters → 18 verified
live boards with GTA postings, none previously configured. Added to Casey's config
(hand-edited to preserve comments — NOT `config seed --apply`, which would strip the G1
Job Bank comment block), the committed seed file (with refreshed curation rationale), and
verify_seeds.py CANDIDATES. Suite 757 green; ruff clean. Live E2E scan: greenhouse
321→500, ashby 84→246, lever 17→88 GTA-eligible ingested; **+27 net-new DB rows / ~22
survivable GTA roles** at target employers (Wealthsimple, 1Password, Geotab, Instacart,
Hootsuite, Knix, Achievers, Docebo, Clutch, Mejuri). G2's non-eng filter fired live (78
dropped). Rest correctly filtered (694 senior-title — these boards skew Senior+, gated by
include_senior_roles=False).

---

## GTA-exposure initiative — closing summary (2026-06-01)

Goal: maximize GTA-area job exposure. Net result across the queue funnel:
- **G1** Job Bank Canada resurrected as an HTML scraper (RSS dead) → ~14-23 net-new GTA
  roles/scan from a national aggregator of small/mid employers no ATS slug list covers.
- **G2** Non-engineering ingest prefilter (default-on) → ~78 non-eng titles/scan no longer
  burn LLM scoring budget; 0 false positives on real roles.
- **G3** Co-op/intern/campus titles no longer wrongly auto-declined as "Senior-band" →
  recovers the entry-level band most relevant at 3 YoE.
- **G4** SKIPPED — Workable/Recruitee have ~0 discoverable GTA inventory and auto-discover
  already covers them at runtime.
- **G5** +18 verified GTA Greenhouse/Lever/Ashby boards (Wealthsimple, 1Password, Waabi,
  Geotab, Instacart, Hootsuite, Docebo, Hopper, …) + pruned the one genuinely-dead slug.

Levers NOT pulled (available if the queue needs more later): flip
`applicant.include_senior_roles=True` to admit Senior-IC roles from the new AI/Staff-heavy
boards (694/scan currently gated); widen Job Bank role queries; wider Workable/Recruitee
slug hunt. Re-score the backlog if you want existing rows re-evaluated under any change.

---

### Phase D1 — README reorganization (docs-only) ✅

**Goal:** Restructure `README.md` into a command-organized reference (every
subcommand + flag listed, ordered most-used → least-used) and collapse the
duplicated Quickstart / Daily flow / Maintenance(Daily/Weekly) sections into one
streamlined Workflows block.

**Blast radius:** docs-only. Files touched: `README.md`, `IMPLEMENT.md`. No
code, no new interfaces.

**Reuse audit:** N/A — prose reorganization, no new utilities/functions. Command
surface verified against `src/jobhunt/cli.py` + `commands/*.py` (not guessed) —
see the full inventory below.

**Verified command inventory (source of truth for the rewrite):**
- `scan` — `--skip-score` `--skip-ingest` `--limit` `--refresh` `--max-age-days`
  `--no-discover`
- `list` — `--week` `--status` `--min-score` `--source` `--verdict` `--no-reply`
  `--older-than` `--limit`
- `apply <job-id>` — `--top` `--best` `--min-score` `--no-browser` `--set-status`
  `--mark-response` `--mark-interview` `--set-outcome` `--recruiter-type` `--url`
  `--title` `--company` `--no-score` `--force-robots` `--description-from-stdin`
  `--include-borderline`
- `answer "<q>"` — `--job` `--max-words` `--no-save` `--recall`
- `add <URL>` — `--skip-probe`
- `interview-prep <id>` — `--stage` `--research` `--force-robots` `--no-llm`
  `--recruiter-type` `--refresh-research`
- `analyze certs` (`--top` `--trend` `--window-days` `--min-score`) · `skills`
  (`--gaps` `--window-days` `--top`) · `employers` (`--hiring-velocity`
  `--window-days`) · `response-rate` (`--by`) · `validators` (`--window-days`
  `--top`)
- `convert-resume` — `--docx`
- `setup` — (no flags)
- `discover slugs` — `--ats` `--limit` `--apply` `--include-cached`  *(legacy)*
- `config` *(hidden)* — `seed` (`--preview` `--apply`) · `reprobe` (`--prune`
  `--force`) · `show` · `path` · `calibrate`
- `db` *(hidden)* — `init` · `migrate` · `reset` (`--force`)
- Global: `--debug` `--verbose/-v`

**New `README.md` section order:**
1. Title + intro + Non-goals *(keep)*
2. Requirements *(keep)*
3. Install + Ollama systemd settings *(keep)*
4. **Workflows** *(NEW — merges First run + Daily flow + Maintenance)*:
   First run · Daily · Weekly — compact command lists, no prose duplication.
5. **Commands** *(REORGANIZED — per-command, most→least used, every real flag)*:
   `scan` · `list` · `apply` · `answer` · `add` · `interview-prep` · `analyze`
   (5 subcmds) · `convert-resume` · `setup` · `discover slugs` (legacy) ·
   `config` (hidden) · `db` (hidden).
6. Configuration *(keep)*
7. Data layout *(keep)*
8. For maintainers + License *(keep)*

**Decisions made (ambiguous request — correct in next pass):**
- "Streamlined" = condense the three overlapping flow sections (First run / Daily
  flow / Maintenance→Daily+Weekly) into ONE Workflows block, not delete guidance.
- Command order = usage frequency: daily drivers (scan/list/apply) first;
  onboarding/maintenance (setup/discover/config/db) last.
- Surface the **full real flag set** per command (the current README omits several
  real flags, e.g. `apply --mark-response/--mark-interview/--set-outcome`,
  `scan --skip-score/--skip-ingest/--refresh`, `list --status/--limit`,
  `add --skip-probe`).
- Single atomic phase — a prose rewrite must land coherently; splitting mid-way
  leaves the README internally inconsistent (noted exception to diff-surface).

**Verification:**
- Every command/flag in the inventory above appears in §Commands; no invented
  command or flag.
- `git diff README.md` reviewed; visual read of the rendered structure.

**Status:** [ ] not started | [ ] in progress | [x] done (2026-05-31). README
rewritten: §Workflows (First run / Daily / Weekly) replaces the old Quickstart +
Daily flow + Maintenance sections; §Commands is now per-command, ordered
most→least used, every real flag listed inline (no `--help` round-trip needed —
per follow-up). Per-command coverage verified against the inventory above (0
MISSING). config + db documented as hidden internals at the end (user choice).
Kept verbatim: intro, Non-goals, Requirements, Install, Configuration, Data
layout, For maintainers, License. Added IMPLEMENT.md to the maintainers doc list.

---

**Plan "Backfill JD-required verified skills the tailor drops" completed
2026-05-31** (single phase; suite 709). A
2026-05-31 audit of the 14 tailored applications found honesty airtight (0
fabrication / cover-violation / alignment flags) but 3 resumes shipped below the
ATS keyword threshold. The headline case: shyftlabs (score 82, applied 05-31) hit
62% coverage with Git/AWS/Azure "missing" — all three are in `verified.json`
(`skills_data_devops`) and the JD required them, but the tailor consolidated that
bucket into a "Backend & APIs" category and dropped Git/GH-Actions/Jest/AWS/Azure.
Root cause: `_tailor_once` never sees the JD must-haves, so infra/cloud skills
that don't fit the JD-relevant categories fall out — even when required. Fix: a
deterministic post-processor `_ensure_jd_required_skills` that re-adds any
verified skill the JD names but the tailored output omits, mirroring the existing
`_complete_familiar_bucket` backfill. Single phase below. (The 0%/43% cases are
honest non-fits already covered by the block-at-<50% audit floor — out of scope.)

**Plan "Bound LLM generation with a `num_predict` ceiling" completed 2026-05-31**
(single phase; suite 703). A `scan`
right after the calibration fix scored 0/2 — both jobs `ReadTimeout` at 240s.
Diagnosed (2026-05-31): server healthy (trivial call 0.19s), model 100% on GPU,
but `_DEFAULT_OPTIONS` set no `num_predict`, so generation was unbounded to the
16k context. On certain thin JDs qwen3.5:9b ignores `think=false` and reasons
**in-band** — opens a `reasons[]` JSON string and pours a monologue into it,
never closing it (measured: 8000 tokens, `done_reason=length`, 28KB invalid
JSON), exhausting context and hanging the scan. Fix: pin `num_predict=4096` in
`gateway.client._DEFAULT_OPTIONS` — above the largest legit output (tailor ~2.2k
tokens) so nothing real truncates, bounding a runaway to ~50s (fast logged failure
vs 8-min stall). NOTE: this stops the *hang*; pathological JDs still fail to score
(truncated JSON is invalid) — making qwen stop reasoning in-band is a deeper
follow-up (logged in Future work). Single phase below.

**Plan "Fix score calibration — thin-JD inflation cap + backlog re-score"
completed 2026-05-31** (both phases; suite 703). A 2026-05-31 audit of the last 4
scans (232 scored jobs) found the apply queue biased toward under-described jobs:
the same ZoomInfo *Full Stack Engineer* scored 82 from its 500-char Adzuna snippet
vs 55 from the 7,140-char Greenhouse JD. Root cause: the `must_have_count < 3`
carve-out in `pipeline/score.py` skipped the coverage clamp for thin JDs and let
the raw LLM score pass through with no ceiling. Fixed with a length-gated
confidence cap (`thin_jd_score_cap=70`, `thin_jd_chars=800`) + a targeted backlog
re-score (20 rows; after: 0 thin rows above the cap). **Out of scope** (logged,
user-confirmed): min_score re-tune (stays 55) and same-source near-dupe collapse
(deferred — see Future work). Per-phase detail below.
- Phase 1 — Thin-JD confidence cap (code + tests). **Done.**
- Phase 2 — Re-score the affected backlog through the new cap. **Done.**

Plan "Expand `analyze certs` AI/LLM coverage" completed 2026-05-30 (Phase 1) —
`_KNOWN` now covers the 2026 AI/LLM cert landscape; suite 701. The external
cert/skill trends command is logged as future work below (separate plan when
prioritized).

### Phase 1 — Backfill JD-required verified skills (`_ensure_jd_required_skills`)

**Goal:** Guarantee that any verified skill the JD names appears in the tailored
resume, so the tailor can't drop a JD-required skill the candidate actually has.

**Files to touch:**
- `src/jobhunt/pipeline/tailor.py` — new `_ensure_jd_required_skills(tailored,
  verified, job)`; import `phrase_present`/`peer_match` from `pipeline._keywords`;
  call it in `_tailor_once` right after `_complete_familiar_bucket` (before
  `_cap_lead_category_size` / `_shrink_to_one_page`).
- `tests/test_tailor_*.py` — unit tests + a real-artifact regression.

**Functions to add/change:**
- `_ensure_jd_required_skills` — add. For each verified non-Familiar skill
  (`skills_core/cms/data_devops/ai`) the JD names (`phrase_present`/`peer_match`
  vs `title + description`), if it's absent from the flattened tailored resume,
  append it to the non-Familiar category with the most same-bucket siblings
  (fallback: last non-Familiar category; final: a new "Additional" bucket before
  Familiar, mirroring `_cap_lead_category_size`).
- `_tailor_once` — change — insert the call at line ~104.

**Reuse audit:**
- Search terms: `phrase_present|peer_match|_complete_familiar_bucket|
  _extract_must_haves_from_jd|skills_categories` in `pipeline/`.
- Candidates: `_complete_familiar_bucket` (same backfill pattern — mirrored, not
  imported), `audit._extract_must_haves_from_jd` + `audit._resume_text` (the
  exact JD-intersect + blob logic — but `audit` imports `tailor`, so importing
  back is a cycle; reuse the shared `_keywords.phrase_present`/`peer_match`
  primitives instead and inline the ~10-line blob/intersect). No new public
  interface; honest by construction (only ever re-adds verified skills).

**One-page interaction:** runs before `_shrink_to_one_page`, which only trims
summary / Familiar items / role bullets / coursework — never non-Familiar skill
categories — so backfilled skills survive; summary/bullets absorb the pressure.

**Verification (≤3 bullets):**
- New unit tests: a dropped JD-required `data_devops` skill (AWS) is re-added to
  the backend category; a verified skill the JD does NOT name is left out; an
  already-present skill isn't duplicated.
- Real-artifact regression: load the stored shyftlabs `tailored-resume.json` +
  JD, run the backfill, recompute `audit.keyword_coverage` → 62% rises to ≥ the
  soft threshold (deterministic, no Ollama).
- `uv run pytest -q` — full suite green.

**Status:** [ ] not started | [ ] in progress | [x] done (2026-05-31, suite 709;
+6 backfill tests). Real-artifact check (not committed — `data/` is gitignored):
the stored shyftlabs `tailored-resume.json` recomputed from **62% → 100%**
coverage, with Git/AWS/Azure re-added to "Backend & APIs". mypy clean; no new
ruff findings (3 pre-existing UP037 + 1 E501 untouched).

### Phase 1 — Pin `num_predict=4096` in the gateway defaults

**Goal:** Bound LLM generation so an in-band reasoning runaway fails fast instead
of hanging the scan past the 240s timeout.

**Files to touch:**
- `src/jobhunt/gateway/client.py` — add `num_predict: 4096` to `_DEFAULT_OPTIONS`
  (+ comment explaining the in-band-reasoning runaway it guards).
- `tests/test_gateway_errors.py` — extend `test_payload_pins_default_options` to
  assert `num_predict == 4096` is sent.

**Reuse audit:**
- Search terms: `num_predict|_DEFAULT_OPTIONS|presence_penalty|options` in
  `gateway/client.py` + `tests/`.
- Candidates found: existing `_DEFAULT_OPTIONS` dict + `test_payload_pins_
  default_options`. Both reused (extend, no new construct). Per-call `options=`
  override already supported, so future per-task tuning needs no new plumbing.

**Verification:**
- `uv run pytest -q tests/test_gateway_errors.py` — option is sent (6 passed).
- `uv run pytest -q` — full suite green (703).
- E2E (real `score_job` on the formerly-hanging Magna junior-coop JD): now
  **FAILED-FAST in 103.6s** with an unterminated-JSON `GatewayError` instead of a
  240s ReadTimeout hang. The ~103s = the ~50s `num_predict` bound × the one
  invalid-JSON retry `complete_json` does; each generation is bounded to ~50s. The
  job still doesn't score (in-band reasoning → invalid JSON) — that's the logged
  future-work fix, not this phase's goal.

**Status:** [ ] not started | [ ] in progress | [x] done (2026-05-31, suite 703).

### Phase 1 — Cap signal-poor (thin-JD) scores at a confidence ceiling

**Goal:** Stop short/snippet JDs that yield `<3` must-haves from passing the raw
LLM score through unbounded, so they can't outrank fully-described full-JD roles.

**Files to touch:**
- `src/jobhunt/config.py` — add `thin_jd_score_cap` + `thin_jd_chars` to
  `PipelineConfig`.
- `src/jobhunt/pipeline/score.py` — length-gate the `must_have_count < 3` branch.
- `tests/test_score_clamp.py` — repurpose the tiny-denominator test + 2 new cases.

**Functions to add/change:**
- `pipeline.score.score_job` — change — the `must_have_count < 3` branch now caps
  at `cfg.pipeline.thin_jd_score_cap` when `len(description) <
  cfg.pipeline.thin_jd_chars`; long JDs with few must-haves still trust raw.
- `config.PipelineConfig` — add `thin_jd_score_cap: int = 70`,
  `thin_jd_chars: int = 800`.

**Reuse audit:**
- Search terms: `clamp|cap|thin|snippet|raw_score|must_have_count` in
  `pipeline/score.py` + `tests/test_score_clamp.py`.
- Candidates found: existing `must_have_count < 3` carve-out branch;
  `_clamp_by_coverage`; `_all_matched_are_familiar`.
- Why not reused: the carve-out branch **is** reused (its body changes, no new
  function). `_clamp_by_coverage` caps by coverage denominator — wrong tool for
  the `<3` signal-poor case (that's exactly why the carve-out skips it). New
  config fields follow the `min_score` pattern. No new public interface.

**Verification:**
- `uv run pytest -q tests/test_score_clamp.py` — cap fires (78→70), long-JD
  exempt (82 stays), below-ceiling unchanged (64 stays), `≥3`-must-have clamp path
  intact.
- `uv run pytest -q` — full suite green.

**Status:** [ ] not started | [ ] in progress | [x] done (2026-05-31, suite 703;
+2 net tests; the tiny-denominator test was repurposed to assert the cap fires).

### Phase 2 — Re-score the affected backlog through the new cap

**Goal:** Correct today's queue (a code-only edit doesn't change `prompt_hash`,
so existing inflated rows won't auto-re-score).

**Files to touch:** none (operational — DB + scan run). Risky tier: back up
`data/jobhunt.db`, confirm the affected row count before deleting.

**Steps:** back up DB → preview `length(description) < 800 AND score > 70` →
`DELETE FROM scores` for that set → `uv run jobhunt scan --max-age-days 0`
(re-scores the now-unscored rows through Phase 1's cap; needs Ollama).

**Verification:**
- No `length(description) < 800` row scores above `thin_jd_score_cap` (70).
- Re-run the audit queries; 70+ band no longer dominated by 500-char snippets.
- Spot-check ZoomInfo + Magna.

**Status:** [ ] not started | [ ] in progress | [x] done (2026-05-31).
Executed as a **targeted in-place re-score of the 20 affected rows** (thin JD +
score > 70) via the exact `score_job → write_score → set_decline_reason` path,
rather than a full network `scan` — avoids fresh-ingest + `config.toml`
auto-discover churn and gives a clean before/after on the same rows. DB backed up
first (`data/jobhunt.db.bak-20260531-075606`). Results: 19/20 capped to ≤70 (most
to exactly 70; Magna 76→62, Finlink 72→62 re-scored below the cap on their
merits). 1 row (`adzuna_ca:5737493705`, Lioness AWS Backend Developer) timed out
at temp=0 on every attempt — deleted its score row so it's UNSCORED (no fabricated
number; re-scores on the next clean scan) rather than left at the inflated 72.
**After:** 0 thin rows above the cap; every score > 70 is a full-JD role; thin
500-char snippets pinned at exactly 70 (e.g. ZoomInfo snippet 82→70 while its
Greenhouse full JD stays 55). The `82×17` inflation cluster is gone.

<details><summary>Completed plan detail (Phase 1)</summary>

**Plan was: Expand `analyze certs` AI/LLM coverage +
log an external-trends command as future work.** Goal is twofold (both equally,
per session Q&A): improve cert *detection coverage* (the `_KNOWN` list has
essentially no 2026 AI/LLM certs) and, because added `_KNOWN` entries feed the
existing `_classify_verdict` path automatically, improve the *personal-fit
verdict* (`--min-score`) for the AI/LLM career direction. This stays fully
inside the AGENTS.md `analyze` contract: deterministic, regex + counters, **zero
network, zero LLM**. The external-trends idea (scanning sources outside the DB)
is real but is a *different surface* — it cannot live in `analyze` without
breaking that contract, so it's logged as a future plan, not built here.

**Decisions made (from session Q&A — correct if wrong):**
- **Phased.** Phase 1 = in-DB cert coverage now (small, safe, immediate). The
  external `research`-style command is logged as "Future work", scoped later as
  its own plan with its own network/robots/caching design.
- **AI/LLM focus** for the `_KNOWN` additions (matches the career-direction
  research). Cert names/codes verified via web search 2026-05-30 (AWS, Azure,
  Databricks, NVIDIA, Google Cloud sources).
- **No rubric change.** `_classify` / `_classify_verdict` are untouched — adding
  detectable certs is a *data* change, not a logic change, so the frozen,
  audit-traceable verdict rules stay frozen.
- **Match strictness: canonical name + exam code only** (session Q&A). Each new
  entry matches the full official name OR its exam code (`AIF-C01`, `MLA-C01`,
  `MLS-C01`, `AI-102`, `NCA-GENL`). NO loose shorthand (`Databricks GenAI`,
  `AWS AI Practitioner` without `Certified`) — preserves the curated-precision,
  low-false-positive spirit of `_KNOWN`.
- **TensorFlow Developer Certificate left in place.** It's discontinued (2026)
  but still in `_KNOWN` (line ~135); kept so legacy JDs still match. Pruning is
  out of scope for this plan.

**Previous plan:** "Harden `scripts/bench_models.py`" completed 2026-05-30
(Phases 1–3); 5-model run done — `qwen3.5:9b` confirmed as the default (see
memory `model-bench-2026-05`). Next bench-related human step (optional):
`--mode production --runs 5` if a closer re-rank is ever wanted.

### Phase 1 — Add 2026 AI/LLM certifications to the `_KNOWN` detector

**Goal:** Extend `analyze.certs._KNOWN` so the detector recognizes the current
AI/LLM certification landscape.

**Files to touch:**
- `src/jobhunt/analyze/certs.py` — add AI/LLM entries to `_KNOWN`.
- `tests/test_certs.py` — add one assertion per new cert + an
  overlap-ordering assertion for the Databricks specific-vs-generic case.

**Functions to add/change:**
- `_KNOWN` (`certs.py`) — change — append a `--- AI / LLM ---` block. Verified
  names/codes (each with a code-or-name regex, mirroring the existing AWS/Azure
  style; specific variants placed BEFORE any base so the overlap-filter keeps
  the longest match):
  - AWS Certified AI Practitioner (`AIF-?C01` | `AWS Certified AI Practitioner`)
  - AWS Certified Machine Learning Engineer – Associate (`MLA-?C01` | name)
  - AWS Certified Machine Learning – Specialty (`MLS-?C01` | name)
  - Azure AI Engineer Associate (`AI-?102` | `Azure AI Engineer`)
  - Databricks Certified Generative AI Engineer Associate (name) — placed BEFORE
    the existing generic `Databricks Certified` (line ~131) so the longer match
    wins.
  - Databricks Certified Machine Learning Associate / Professional (names) —
    same ordering note.
  - NVIDIA-Certified Associate: Generative AI LLMs (`NCA-?GENL` | name)
  - Google Cloud Generative AI Leader (name)
  - Google Professional Machine Learning Engineer (name; distinct from the
    existing GCP Data Engineer / Cloud Architect entries)
  - No change to `extract_certs` / `tally` / generic patterns — they consume
    `_KNOWN` as-is.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `grep -n "Azure AI-900\|Databricks Certified\|TensorFlow\|_KNOWN" src/jobhunt/analyze/certs.py`
- Candidates found: existing `_KNOWN` (Azure AI-900 line 59, generic
  `Databricks Certified` line 131, TensorFlow Developer Certificate line 135).
- Why not reused: AI-900 (Fundamentals) and the generic Databricks entry are
  real but don't cover the new Engineer/Specialty/GenAI credentials; the new
  entries extend the same list with the same `(name, _pat(...))` shape — no new
  helper or pattern machinery needed. The generic Databricks entry is *kept*;
  new specific Databricks certs are ordered before it (overlap-filter rule).

**Verification:** (≤ 3 bullets)
- `uv run pytest tests/test_certs.py -q` — new per-cert assertions pass;
  the Databricks overlap test confirms the specific name wins over generic.
- `uv run ruff check src/jobhunt/analyze/certs.py` clean.
- No regression: full `uv run pytest -q` stays green (currently 688).

**Status:** [x] done

**Result (2026-05-30):** Added a `--- AI / LLM (2026) ---` block to
`analyze.certs._KNOWN` (9 entries: AWS AI Practitioner/AIF-C01, AWS ML
Engineer–Associate/MLA-C01, AWS ML–Specialty/MLS-C01, Azure AI Engineer
Associate/AI-102, Databricks GenAI Engineer Associate, Databricks ML
Associate + Professional, NVIDIA NCA-GENL, GCP Generative AI Leader, Google
Professional ML Engineer). Canonical-name-OR-exam-code only, no shorthand.
Databricks specifics ordered before the generic `Databricks Certified` entry.
Added a parametrized `test_ai_llm_certs` (12 cases) + `test_databricks_genai_wins_over_generic`
(single hunk, test_certs.py lines 58–102).
Verified: 9/9 new certs match exactly one entry in isolation (count=1, no
double-count); a realistic multi-cert JD detects all 5 present certs as KNOWN
with the generic tier producing only non-colliding review-list phrases
(`no_known_in_generic=True`, `generic_count=0`); `tests/test_certs.py`
**67 passed**; full suite **701 passed** (was 688, +13 new cases). No rubric
change — `_classify`/`_classify_verdict` untouched, so the new certs flow
through the existing `--trend`/`--min-score` verdict path automatically.

**Lint:** `ruff check src/jobhunt/analyze/certs.py` → **clean**. My test hunk
adds zero new violations. NOTE: `ruff check tests/test_certs.py` reports 8
**pre-existing** errors (E501 long en-dash lines 17/18/51/152/153; I001 +
SIM105 in the existing `_render_snapshot` test at 342–348) — all outside my
58–102 hunk, confirmed by a stash round-trip. The Phase-1 plan's "ruff clean"
verification was an incorrect assumption: the file was already dirty. Per the
no-piggybacking rule these are NOT fixed here; logged as optional cleanup
below.

**Pillar-4 docs:** README/PLAN/AGENTS unchanged — `analyze certs` has no new
flags or output-format change; only detector coverage grew (a data change).

**Plan complete.** External cert/skill trends command remains logged as future
work below.

Note (env): the en-dash (U+2013) in AWS cert names triggered terminal
output-doubling during verification; all checks were confirmed via file-based
reads, not streamed stdout.

</details>

### Future work (not in this plan — separate future plan)

- **Make in-band-reasoning JDs actually score (not just fail fast).** The
  `num_predict=4096` cap (2026-05-31) stops the *hang*, but JDs where qwen3.5:9b
  reasons inside a JSON string still fail to score (truncated JSON is invalid).
  Candidate fixes, each its own plan: (a) tighten the score schema (cap
  `reasons[]`/`gaps[]` `maxItems` so the grammar can't accept an endless string —
  verify Ollama's `format` honors `maxItems`); (b) a small score-only
  `presence_penalty` via per-call `options=` (Qwen's anti-reasoning-loop knob,
  but re-tune against structured-JSON repetition first — it's load-bearing at 0,
  see `_DEFAULT_OPTIONS`); (c) detect `done_reason=length`/parse-fail and retry
  with a corrective hint. Pick after observing how often it actually bites.
- **Pre-existing lint cleanup in `tests/test_certs.py`** (optional, own phase).
  8 ruff errors predating this work: E501 on the en-dash AWS-cert lines
  (17/18/51/152/153), I001 import-sort + SIM105 try/except in the
  `_render_snapshot` test (342–348). Trivial-tier; do as a standalone commit,
  not bundled into a feature.
- **External cert/skill trends command.** A NEW network-touching command (e.g.
  `jobhunt research trends`) that pulls cert/skill demand signal from sources
  *outside* the scanned-jobs DB. Must live OUTSIDE `analyze` (the analyze
  contract forbids network/LLM). Net-new infra: HTTP via `jobhunt.http`,
  source selection, `data/cache/` TTL caching, `robots.txt` respect, and the
  standing no-LinkedIn/Indeed/Glassdoor rule still binds. Scope as its own plan
  with its own phases when prioritized.

<details><summary>Completed plan detail (Phases 1–3)</summary>

The bench previously exercised only 4 of 5 LLM task
slots (score/tailor/cover/answer — **interview-prep is missing**) against a
single happy-path fixture JD that can never trigger the score auto-decline or
the tailor/cover fabrication guards. For a reliable product the bench must
measure both the slots *and* the safety guards that protect honesty/accuracy —
not just first-pass formatting on an easy JD. A model could ace the friendly JD
and still hallucinate interview anchors, wrongly accept a senior/management
role, or fabricate unverified skills under pressure, and the current bench would
never see it.

**Decisions made (from this session's Q&A — correct if wrong):**
- **Interview-prep slot added to BOTH modes** (raw + production). Production uses
  the real `pipeline.interview_prep.draft_prep_with_retry`; raw threads per-model
  `~/ai` options through a thin inline shim that reuses `validate_prep_sections`
  + the prep decode helpers (mirrors the existing inline cover/answer raw shims —
  no `src/` change).
- **Two separate adversarial fixtures**, not one combined: a *decline* JD
  (senior + people-management + over-YoE) to test the score auto-decline, and a
  *fabrication-pressure* JD (good-fit level but demands unverified watchlist
  skills like Kubernetes/Go/GraphQL/Kafka) to test the tailor/cover honesty
  guards + retry. Separate fixtures isolate the two signals — a combined JD that
  declines would skip nothing in the bench (it tailors regardless) but would
  conflate "did it decline?" with "did it fabricate?" on one row.
- **Bench-only, no runtime change.** Read-only on the DB; swapping the
  configured default model stays a later, human-gated decision.
- **Single profile** (Casey's `verified.json`) — only one user; multi-profile
  coverage is explicitly out of scope.
- A **decline** is measured as `ScoreResult.decline_reason is not None OR
  score < cfg.pipeline.min_score` (the apply-loop gate). On the fabrication
  fixture, a tailor `FabricationError` (guard rejected after retries) is a SAFE
  outcome, reported distinctly from a plain error.

**Pillar-4 doc impact:** `README.md` and `PLAN.md` need no change (the bench is
a manual dev script, not user-facing surface or app architecture). The in-file
docstring + `Notes` block are updated each phase. AGENTS.md "Testing" already
covers "Pipeline integration (real Ollama) — manual; not in CI"; no change
needed.

### Phase 1 — Add the interview-prep slot to the bench (both modes)

**Goal:** Measure the interview-prep LLM slot per model in both raw and
production bench modes.

**Files to touch:**
- `scripts/bench_models.py` — add a prep pass to `_bench_one_run` (raw) and
  `_bench_one_run_production` (production); add prep metrics + table rows.

**Functions to add/change:**
- `_bench_one_run` — change — add a raw prep pass: build a `PrepContext`, call a
  thin inline shim (render the interview-prep prompt → `complete_json(options=…)`
  → `_decode_sections`), then `validate_prep_sections`; record latency +
  first-pass clean + violation count. Inline mirrors the existing cover/answer
  raw shims so per-model `~/ai` options thread through.
- `_bench_one_run_production` — change — add
  `draft_prep_with_retry(cfg, ctx=…, verified=…, max_attempts=cfg.pipeline.cover_retry_attempts)`;
  record latency, eventual-clean, attempts.
- `ModelMetrics` — change — add `prep_latencies`, `prep_validator_clean`,
  `prep_violation_counts`, `prep_attempts`.
- `_print_table` — change — add Prep lat / Prep clean / Prep violations (avg) /
  Prep attempts (avg) rows.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `grep -nE "draft_prep|validate_prep|PrepContext|_decode_sections" src/jobhunt/pipeline/interview_prep.py`;
  `grep -n "answer_latencies|_bench_one_run" scripts/bench_models.py`
- Candidates found: `pipeline.interview_prep.draft_prep_with_retry` /
  `draft_prep_sections` / `validate_prep_sections` / `_decode_sections` /
  `PrepContext` / `build_interview_context`; the existing answer-slot bench block.
- Why not reused wholesale in raw mode: `draft_prep_sections` doesn't accept
  per-model `options` (the same constraint that made cover/answer inline in raw
  mode), so the raw pass replicates only the render+call shim while reusing the
  decode + validate helpers. Production mode reuses `draft_prep_with_retry` as-is.

**Verification:** (≤ 3 bullets)
- `uv run ruff check scripts/bench_models.py` clean.
- `uv run python scripts/bench_models.py --mode production --models qwen3.5:9b --runs 1`
  prints a non-zero Prep row + Prep attempts.
- `--mode raw --models qwen3.5:9b --runs 1` prints a Prep row at params src
  `~/ai:build-qwen`.

**Status:** [x] done

**Result (2026-05-29):** ruff clean. `--mode production` qwen×1 shows a Prep row
(lat 132.3s, attempts 3.0, 1 residual violation → clean 0%); `--mode raw` shows
a Prep row at params src `~/ai:build-qwen` (lat 22.0s). All four prep metric
rows (lat / clean / violations / attempts) render in both modes. qwen's prep
hitting the retry cap with a residual violation is real model signal — exactly
the kind of reliability gap this slot was added to surface, not hide. Raw mode
reuses the inline-shim pattern (render_user + complete_json(options=…) +
`_decode_sections` + `validate_prep_sections`); production reuses
`draft_prep_with_retry` as-is. No `src/` change.

### Phase 2 — Generalize the bench to a fixture list

**Goal:** Run every model/slot over a fixture list with per-fixture reporting,
using only the existing happy-path fixture.

**Files to touch:**
- `scripts/bench_models.py` — introduce a `BenchFixture` dataclass; thread a
  `fixture` arg through both per-run fns; aggregate + print metrics per fixture.

**Functions to add/change:**
- `BenchFixture` — add — `key`, `job: Job`, `question: str`,
  `expect_decline: bool=False`, `expect_fabrication_pressure: bool=False` (the
  latter two are defined now, consumed in Phase 3).
- `DEFAULT_FIXTURES` — add — one entry wrapping today's `FIXTURE_JOB` +
  `FIXTURE_QUESTION` (`expect_decline=False`).
- `_bench_one_run` / `_bench_one_run_production` — change — accept
  `fixture: BenchFixture`; read job/question from it instead of the module
  globals.
- `main` — change — loop models × fixtures; key metrics by
  `(label, fixture.key)`.
- `_print_table` — change — print one block per fixture with a fixture header.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `grep -n "FIXTURE_JOB|FIXTURE_QUESTION|ModelMetrics|_print_table" scripts/bench_models.py`
- Candidates found: existing `FIXTURE_JOB` / `FIXTURE_QUESTION` module globals;
  `ModelMetrics`; `_print_table`.
- Why not reused as-is: the single module-global fixture is exactly what blocks
  multi-fixture coverage; this phase promotes it into a list entry without
  changing its content (walking skeleton — output for N=1 matches Phase 1 modulo
  a fixture header).

**Verification:** (≤ 3 bullets)
- `uv run ruff check scripts/bench_models.py` clean.
- `--mode production --models qwen3.5:9b --runs 1` prints one fixture block whose
  numbers match Phase 1's single-fixture output.
- `--mode raw` still prints per-model params src.

**Status:** [x] done

**Result (2026-05-30):** ruff clean; 688 tests pass (unchanged). Both per-run
fns + `main` + `_print_table` now take a `fixture: BenchFixture`; module globals
`FIXTURE_JOB`/`FIXTURE_QUESTION` survive only as the one `DEFAULT_FIXTURES`
entry. `--mode production` and `--mode raw` (qwen×1) each print a single
`FIXTURE: happy_fit` block whose slot numbers match Phase 1. Model loop stays
outer (one cold load per candidate across all fixtures). `expect_decline` /
`expect_fabrication_pressure` fields defined but unused until Phase 3.

### Phase 3 — Add the decline + fabrication-pressure fixtures with guard metrics

**Goal:** Add the two adversarial fixtures and report whether each guard fired
correctly.

**Files to touch:**
- `scripts/bench_models.py` — add the two `BenchFixture` entries +
  guard-correctness metrics/rows.

**Functions to add/change:**
- `DEFAULT_FIXTURES` — change — add `decline_senior` (`expect_decline=True`) and
  `fabrication_pressure` (`expect_fabrication_pressure=True`).
- `_bench_one_run_production` / `_bench_one_run` — change — record
  `score_declined` (`decline_reason is not None or score < cfg.pipeline.min_score`);
  on a fabrication fixture, classify a tailor `FabricationError` as a SAFE
  rejection (separate counter), not a plain error.
- `ModelMetrics` — change — add `score_declines: list[bool]`,
  `tailor_safe_rejections: int`.
- `_print_table` — change — add a per-fixture "Guard" summary: decline fixture →
  `declined X/N`; fabrication fixture → `fab-safe X/N (recovered Y / rejected Z)`;
  happy fixture → `not-declined X/N`.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `grep -n "decline_reason|FabricationError|min_score" src/jobhunt/pipeline/score.py src/jobhunt/pipeline/tailor.py`
- Candidates found: `ScoreResult.decline_reason`, `cfg.pipeline.min_score`,
  `pipeline.tailor.FabricationError`.
- Why not reused elsewhere: these are read-only signals; the phase only
  interprets them — no new guard logic, the guards live in `src/` and stay
  untouched.

**Verification:** (≤ 3 bullets)
- `uv run ruff check scripts/bench_models.py` clean.
- `--mode production --models qwen3.5:9b --runs 1`: decline fixture shows
  `declined 1/1`; fabrication fixture shows `fab-safe 1/1`; happy fixture shows
  `not-declined 1/1`.
- Existing happy-fixture numbers unchanged from Phase 2.

**Status:** [x] done

**Result (2026-05-30):** ruff clean; 688 tests pass (unchanged). Added
`decline_senior` (`expect_decline`) + `fabrication_pressure`
(`expect_fabrication_pressure`) fixtures, a `score_declines`/`tailor_safe_rejections`
metric pair, a `FabricationError`-as-SAFE-rejection branch in both per-run fns,
and a `Guard` table row via `_guard_summary`. Verified qwen×1:
- **production:** happy_fit → `not-declined 1/1` (ship); decline_senior →
  `declined 1/1` (audit block); fabrication_pressure → `fab-safe 1/1 (clean 1 /
  rej 0)` — tailor omitted the unverified skills.
- **raw:** same Guard verdicts, but fabrication_pressure → `fab-safe 1/1
  (clean 0 / rej 1)` — first-pass tailor leaked an unverified skill and the
  guard rejected it; production's retry loop recovers it to clean. The
  raw-vs-production split is exactly the reliability signal the adversarial
  fixtures were added to surface.

**Plan complete.** The bench now covers all 5 LLM slots
(score/tailor/cover/answer/interview-prep) across 3 fixtures
(happy/decline/fabrication) in both modes, with explicit guard-correctness
reporting. Next human step (unchanged intent): run
`--mode production --runs 5` across qwen/granite/llama/ministral/gemma and read
the per-fixture Guard rows + eventual ship-rate before any `gateway.tasks`
default-model change. A candidate that fits the decline JD or fabricates on the
pressure JD is disqualified regardless of happy-path latency.

</details>

Standing context that isn't obvious from the code:
- `kb/profile/` is gitignored PII (regenerated by `jobhunt convert-resume` from
  `Resume.docx`, now the single source of truth — all current skill/tier state
  is encoded in the doc, so a regen is safe).
- Skill honesty tiers: **Core** (paid production) / **Familiar** (light/academic
  — Java, Spring Boot, Angular, MCP, Agile, Headless, Figma, Astro). There is no
  Projects tier (added then removed May 2026 — Astro moved to Familiar).
- **React umbrella:** verified Core skill is `React (Redux, Native)`; renders as
  plain "React" unless a JD names Redux/React Native (then surfaced, Core-grade).
  No standalone Redux item.
- `include_senior_roles=false` in `~/.config/jobhunt/config.toml` (Casey <3 YoE).
- Default model: bare `qwen3.5:9b`; the gateway pins app-owned options
  (`num_ctx=16384` + samplers) on every call.

## Completed plans (archive)

Terse index; see git history for the full phase-by-phase detail that used to
live here.

- **bench_models.py production mode** (2026-05-29). Refreshed the model bench to
  the 5 base models with `--models`/`--runs`, a per-model `~/ai` PARAMS pull
  (`--mode raw`), the `answer` slot, and a `--mode production` path running the
  real retry-wrapped pipeline (`score_job` → `*_with_retry` → `audit`) at
  `_DEFAULT_OPTIONS` for eventual ship-rate + attempts. Phases 1–4.
- **Remove dormant Projects tier + docs audit** (2026-05-28). After Astro moved
  to Familiar, stripped the empty `skills_projects` tier from code (guard, score
  cap, shrink ladder, parser), tests, and prompt/policy docs; kept the React
  umbrella + `_PAGE_SAFETY_MARGIN`. Audited the 4 pillars: fixed PLAN.md
  (qwen3.5:9b default, app-owned `num_ctx`) and an AGENTS.md systemd-comment
  contradiction. Suite 688.
- **React umbrella** (2026-05-28). Collapsed React/Redux/React Native into one
  Core umbrella skill `React (Redux, React Native)`; renders as "React" by
  default, surfaces Redux/React Native only when the JD names them. Rules in
  `tailor.md` / `tailoring-rules.md` / `Resume_Tailoring_Instructions.md` /
  AGENTS.md. Suite 693. *(A portfolio Projects-**section** feature was scoped
  then declined — Casey keeps resumes experience-forward; portfolio stays in
  the contact-line links.)*
- **Projects skill tier** (2026-05-28). Added the `skills_projects` honesty tier
  between Core and Familiar — wired through the fabrication guard + Familiar-only
  score cap, the one-page shrink ladder (`_PROJECTS_FLOOR`), prompt/policy docs,
  and `parse_docx` (with a `_PAGE_SAFETY_MARGIN` fix after a real `.docx`
  overflowed). Suite 692.
- **base qwen3.5:9b + app-owned options** (2026-05-28). Gateway pins
  `_DEFAULT_OPTIONS` (num_ctx 16384 + samplers) on every call so structured-task
  behavior is in-repo; switched default model to bare `qwen3.5:9b`. Root cause
  of prose-not-JSON was unset `OLLAMA_CONTEXT_LENGTH` → 4096 truncation.
- **Workday CXS GTA-targeted scan** (2026-05-28). Large Workday boards issue a
  deduped `_GTA_SEARCH_TERMS` union instead of a blank first-100 walk so GTA
  roles aren't buried. `_BLANK_SCAN_MAX=200`.
- **Post-audit fixes** (2026-05-27). `HARD_COVERAGE_FLOOR_PCT=50` escalates
  sub-50% coverage to `block`; `_OVERREACH_PATTERNS` in `cover_validate` catches
  framing-level capability claims; baseline `Resume.docx` summary rewrite.

## Initiative: Positioning resync + parser lock + data-driven core (2026-06-05)

Casey re-positioned his resume and LinkedIn around the specialist lane
(E-Commerce / Headless CMS, Shopify, Contentful, HubSpot, AI as the
differentiator). Three goals, in priority order: (1) make the scoring and
tailoring pipeline actually run on the new positioning, (2) prove
`convert-resume` parses the new skills correctly and the ATS config matches the
lane, (3) clean up untrue docs and remove the hard-coded "Casey" from runtime
code so another developer can drop in their own `Baseline_Resume.docx`. Scope
decision: **data-driven core** (genericize the behavioral hard-codings, keep GTA
scope and Casey's data, no README rewrite or location parameterization).

Findings that motivate the phases:
- `kb/profile/verified.json` is **stale**: still "Full-Stack Developer" summary,
  "Web Developer (Contract)" titles, "(NDA)" employers. The docx has the new
  positioning. The pipeline is scoring on the old framing.
- The parser is correct: `_split_skills` is paren-aware, so PLAN.md's "skills_ai
  produces a run-on, patch by hand" warning is stale doc, not a live bug.
- Behavioral hard-codings of Casey live in `pipeline/cover.py` (sign-off regex),
  `pipeline/audit.py` (`_PROJECT_ANCHORS` frozensets), `pipeline/tailor.py` (IC
  prompt hint), and `cli.py` (help text).

### Phase 1 — Resync verified.json from the updated baseline docx

**Goal:** Regenerate `kb/profile/*` from the current `Baseline_Resume.docx` so the
pipeline runs on the new specialist positioning.

**Files to touch:**
- `kb/profile/verified.json` + sidecar `.md` (generated by `convert-resume`)

**Functions to add/change:** none (run `jobhunt convert-resume`).

**Reuse audit:** n/a (existing command).

**Verification:**
- `convert-resume` prints 0 parse warnings.
- `verified.json` summary starts "E-Commerce", `work_history[0].title` is the
  retitled Shopify role, agency/gaming employers read "(Confidential)", Figma is
  in `skills_core`, "Dawn" is in the `skills_cms` Shopify entry.

**Status:** [ ] not started

### Phase 2 — Verify honesty checks survive the resync

**Goal:** Confirm score + tailor + audit still pass on one real JD after the
resync, with no fabrication regression from the employer renames.

**Files to touch:** none expected (audit anchors key on content tokens, not the
renamed employer field, so they should hold).

**Verification:**
- Run the tailor pipeline on one Shopify JD and report the audit verdict
  (expect ship/revise, not a fabrication block) and that no `FabricationError`
  is raised.

**Status:** [ ] not started

### Phase 3 — Lane-align job_bank_ca queries

**Goal:** Replace the generic full-stack/front-end Job Bank queries with the
Shopify / Contentful / HubSpot / e-commerce lane.

**Files to touch:**
- `~/.config/jobhunt/config.toml` (user state, gitignored) — `[ingest] job_bank_ca`

**Verification:**
- `jobhunt config show` lists the new lane queries.

**Status:** [ ] not started

### Phase 4 — Lock parser correctness with a regression test

**Goal:** Add a test asserting `convert-resume` on the real `Baseline_Resume.docx`
yields atomic skill buckets, Figma in Core, Dawn captured, and parsed projects.

**Files to touch:**
- `tests/test_parse_docx.py` — new test(s) against the `BASELINE` constant

**Functions to add/change:**
- new test functions only

**Reuse audit:** uses existing `BASELINE`/`parse_baseline`.

**Verification:**
- New test passes; it fails if `skills_ai` becomes a run-on or Figma/Dawn drop.

**Status:** [ ] not started

### Phase 5 — Doc-truth pass

**Goal:** Correct stale forward-looking claims so docs match code (the skills_ai
"patch by hand" warning, the "2x experience" auto-decline now YoE+3).

**Files to touch:**
- `PLAN.md`, `AGENTS.md` (forward-looking claims only, never historical logs)

**Verification:**
- grep shows no "patch by hand" / "2x" stale claims; prose matches code.

**Status:** [ ] not started

### Phase 6 — Data-drive the cover sign-off name from verified.json

**Goal:** Replace `cover.py`'s hard-coded `casey hsu` sign-off regex with the
name parsed from `verified.json`.

**Files to touch:**
- `src/jobhunt/pipeline/cover.py` (+ `cover_validate.py` if it shares the regex)
- `tests/test_cover_*.py`

**Functions to add/change:**
- sign-off detect/strip — change — derive the name from the profile

**Reuse audit:** `verified.json` `name` is already loaded by the pipeline.

**Verification:**
- A test with a non-Casey name strips/detects the sign-off correctly.

**Status:** [ ] not started

### Phase 7 — Data-drive audit alignment anchors from verified.json

**Goal:** Build `_PROJECT_ANCHORS` from `verified.json` work-history employers and
projects instead of the hard-coded frozensets.

**Files to touch:**
- `src/jobhunt/pipeline/audit.py`
- `tests/test_audit.py`

**Functions to add/change:**
- add a deriver from `VerifiedFacts`; the anchor-consumption logic is unchanged

**Reuse audit:** reuses the existing alignment-flag consumer; only the source of
anchors changes.

**Verification:**
- New test derives anchors from a sample profile; existing alignment tests pass.

**Status:** [ ] not started

### Phase 8 — Genericize remaining behavioral Casey strings

**Goal:** Derive the tailor IC-level prompt hint and the CLI help text from
config/profile instead of naming Casey.

**Files to touch:**
- `src/jobhunt/pipeline/tailor.py` (IC prompt hint from `applicant.*`)
- `src/jobhunt/cli.py` (generic help)

**Verification:**
- grep shows no user-facing/behavioral "Casey" outside comments; suite green.

**Status:** [ ] not started

## Phase template

Copy this block for each phase of an approved plan. One phase = one atomic,
revertable commit (see Phase-Sizing Rules in `AGENTS.md`).

```
### Phase <N> — <one-sentence goal, no "and">

**Goal:** <single declarative sentence>

**Files to touch:**
- path/to/file — what changes

**Functions to add/change:**
- module.function — add | change — what it does

**Reuse audit:** (per Reuse-First Rule in AGENTS.md)
- Search terms: `<grep/rg terms used>`
- Candidates found: <list, or "none">
- Why not reused: <reason per candidate>

**Verification:** (≤ 3 bullets)
- <how to prove it works — test name or manual E2E output>

**Status:** [ ] not started | [ ] in progress | [ ] done
```
