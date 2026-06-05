# WORK.md — Casey Hsu Projects and Education Knowledge Base

*Purpose: this file is the canonical, honest record of Casey's personal projects
and schooling. It exists to fill the gap that `Baseline_Resume.docx` does not
carry and to give depth behind Section 2 of
[Resume_Tailoring_Instructions.md](Resume_Tailoring_Instructions.md). When a job
posting touches AI/LLM, local infra, Linux, automation, backend, or data, read
this file to pull the right project and the right coursework, then frame them per
the rules in the tailoring instructions.*

**Honesty contract (same as the tailoring instructions).** Everything here is
real. These are Casey's own GitHub projects and his actual George Brown
coursework. They are genuine work, not employment. Reference them to reinforce
verified skills. Do NOT attach employer-style metrics (users, stars, uptime), do
NOT present them as professional experience, and do NOT promote a "Familiar"
skill to Core because a project touched it. The single source of truth for the
automated pipeline stays `kb/profile/verified.json`. This document is the human
and agent reference behind it.

**Related files:**
- [Resume_Tailoring_Instructions.md](Resume_Tailoring_Instructions.md) — the
  operating manual for tailoring. Section 2 lists the short project and
  coursework summaries. This file is their long form.
- [AGENTS.md](AGENTS.md) — pipeline rules, including how project skills now flow
  through `verified.json` (the Projects-into-profile initiative in
  [IMPLEMENT.md](IMPLEMENT.md)).
- `Baseline_Resume.docx` — the master baseline resume. It is not page-limited and
  carries a curated subset of these projects, not all of them. The one-page
  constraint applies only to the tailored output the pipeline generates per job.

---

## 1. Projects (the main focus)

These are the proof that Casey's AI / LLM / infra / automation claims are real.
Most juniors have tutorials. Casey has shipped and deployed systems he can
explain end to end. Each entry below gives the stack, what it is, what hiring
signal it backs, and whether it is shipped or in progress.

**Which projects are on the resume.** The `Baseline_Resume.docx` PROJECTS
section currently carries four (jobhunt, Auto-Agent, SEO-LLM, AI Context Stack).
The rest live here only and surface in cover notes or tailored summaries when the
JD warrants. The matching `Project Stack:` skills row in `Baseline_Resume.docx` (FastAPI,
Redis, Claude API, Docker Compose, JSON-LD, Agentic Architecture) is what makes
these skills creditable in scoring.

### jobhunt — Job Hunt AI Buddy (shipped)

- **Repo:** `github.com/SimBuds/Jobhunt`
- **Stack:** Python, uv, Ollama, SQLite, Playwright, public ATS APIs
- **What it is:** a local-first GTA job-search CLI. It ingests public ATS APIs
  (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Workable, Recruitee, Job
  Bank Canada, Adzuna CA), scores each role against a verified resume with a
  local quantized Ollama model, and drafts tailored resumes and cover letters
  under structural no-fabrication rules. Playwright fills application forms and
  the human submits. No LinkedIn or Indeed scraping, no bot submissions, no
  stored employer credentials.
- **Proves:** AI/LLM application engineering, local LLM hosting, deterministic
  schema-enforced output, Python tooling, browser automation, honesty by
  construction. This is the flagship AI/LLM proof.

### Auto-Agent — Arch autonomous agent stack (deployed, parts in progress)

- **Repo:** `github.com/SimBuds/Auto-Agent`
- **Stack:** FastAPI, Claude API, Postgres, Redis, Docker, Arch Linux, D-Bus
- **What it is:** an automation agent that plans against the Claude API with a
  loopback FastAPI capability server, Postgres durable memory, and a Redis
  context cache, deployed via Docker Compose. 24/7 lingered `systemd --user`
  units boot the stack without a graphical session. Docker-sandboxed containers
  handle tool calls, and the capability server bridges agent intents to
  `notify-send`, `wl-copy`, `xdg-open`, and KDE D-Bus. Exposes system-control
  endpoints for Telegram automation.
- **In progress (name only if the JD asks for in-flight framing, do not present
  as shipped):** Hermes planning layer and the OpenClaw Telegram / Discord
  gateway.
- **Proves:** backend and full-stack product engineering (FastAPI + Postgres +
  Redis + Docker), agentic architecture, Linux and systems depth, automation
  maturity. This is the non-CMS, product-style full-stack proof.

### SEO-LLM — LLM SEO content pipeline (in progress)

- **Repo:** `github.com/SimBuds/SEO-LLM`
- **Stack:** Claude Code, Ollama, Postgres, JSON-LD
- **What it is:** a hybrid Claude Code and local-model SEO pipeline that
  generates content briefs with lint guards for banned words, heading hierarchy,
  meta length, and JSON-LD schema. Google Search Central RSS triggers flag
  content for rule review against search core-update drift.
- **Proves:** LLM application engineering with guardrails, schema and structured
  validation, Postgres data work, technical SEO depth.

### AI Context Stack (shipped)

- **Repo:** `github.com/SimBuds/Ollama-LLM-Prompts`
- **Stack:** Ollama, Modelfile, custom Qwen3.5, Granite4.1, Gemma4, layered
  Markdown
- **What it is:** three custom Ollama models built from one shared
  prompts / memory / knowledge Markdown tree, compiled in a fixed order into a
  generated system prompt plus Modelfile, with project overlays injected at
  request time instead of baked in. Tuned for q5_0 KV cache, flash attention,
  16k context, and thinking mode as an explicit opt-in.
- **Proves:** deep local LLM infrastructure, prompt engineering, GPU and cache
  tuning, model customization. Deep implementation detail like the `q5_0`
  quantization name belongs in a project description or cover note, not the
  one-glance resume skills row.

### macOS Ventura on KVM (shipped, off-resume)

- **Repo:** `github.com/SimBuds/Auto-Agent/projects`
- **Stack:** KVM, QEMU, Quickemu, macOS, Btrfs, Arch Linux
- **What it is:** a battle-tested AMD virtualization path on a Ryzen 5900X plus
  RTX 3080. Quickemu-git / QEMU 11, nocow qcow2 on Btrfs, power-of-two CPU
  topology, CCX-pinned launches via `taskset` to dodge Infinity Fabric latency.
  Software rendering forced Ventura over newer releases.
- **Proves:** Linux and systems depth, virtualization, low-level performance
  tuning. Surface only for infra / Linux / systems-flavored roles.

### Hybrid local+cloud coding agent (in progress, off-resume)

- **Stack:** Claude Code CLI, Ollama (custom `qwen-custom`), Claude API
- **What it is:** a daily-driver developer agent stack. Claude Code CLI as the
  interactive shell, Ollama serving a custom `qwen-custom` (same Modelfile
  assembly pipeline as AI Context Stack) for cheap local turns, with the Claude
  API called for complex multi-file and long-context tasks.
- **Proves:** AI tooling fluency and cost-aware local-plus-cloud orchestration.
  Name only when the JD wants in-flight project framing.

### How to use the projects in tailoring

- They reinforce the baseline AI & Tooling skills row, which is the source of
  truth for what Casey uses daily, and back the AI/LLM differentiator line in the
  Summary. They are concrete proof the Ollama, prompt-engineering, and GPU-tuning
  claims are real.
- Name a project in a tailored Summary or a cover-note lead paragraph when the JD
  asks for AI/LLM, local LLM hosting, Linux or infra fluency, or automation
  maturity. Name it explicitly so an AI-screener summarizer pulls it.
- For full-stack and backend roles, lead with Auto-Agent (FastAPI, Postgres,
  Redis, Docker) and jobhunt as the product-style proof, not the CMS work.
- Do not invent metrics. Describe what the project does, not how popular it is.
- Respect the in-progress labels. Hermes, OpenClaw, SEO-LLM, and the hybrid
  coding agent are not finished products.

---

## 2. Education and school detail

This expands the one-line diploma entry on the resume and Section 2 of the
tailoring instructions. Surface only the coursework that maps to the JD.

### The credential

- **George Brown College, Toronto.** Computer Programming and Analysis, a
  three-year (six-semester) Advanced Diploma. Attended 2021 to 2024, graduated
  April 2024. Dean's List, all terms.
- This is a substantial credential, not a bootcamp. The program is project-based
  and experiential. Over the first two years it builds software application
  development and testing. The final year adds communication, teamwork, and
  client-service practice. The program explicitly covers fast-growing AI and
  machine-learning strategies and development, mobile application development,
  full-stack development, the software development life cycle and methodologies,
  and database management.

### Coursework Casey legitimately completed

Use these only when the matching job skill is asked for. The baseline resume
lists only "Machine Learning, Data Structures and Algorithms, Enterprise Java,
Full-Stack Development." It is honest to surface additional courses from the list
below when they map to a role.

- **Web / Full-Stack:** Introduction to Web Development, Advanced Web
  Programming, Web Application Development, Full Stack Development I and II, Open
  Source Development.
- **Backend / Languages:** Object-Oriented Programming, Application Development
  using Java, Web Application Development Using Java.
- **Data:** Introduction to Data Management, Database Management, Applied Data
  Science, Applied Machine Learning.
- **Mobile:** Mobile Application Development I and II.
- **Engineering practice:** Data Structures and Algorithms, Software Quality
  Assurance, System Analysis Design and Testing, Agile Software Development,
  DevOps, System Development Project, Capstone Project I and II.
- **Systems / Security:** IT Essentials, Linux Essentials, Introduction to Cyber
  Security.

### How to use the schooling in tailoring

- The AI and machine-learning coursework plus the AI/LLM project portfolio
  together make the AI pathway credible. A three-year diploma with formal AI/ML
  coursework counters any "career changer with a quick cert" read.
- Pick 4 to 6 courses that match the JD for the Coursework line. Listing every
  course is noise.
- Mobile, DevOps, Linux Essentials, and Cyber Security are real courses to
  surface for infra, mobile, or platform-flavored roles, even though they are not
  on the baseline resume.

### Certification

- Contentful Certified Professional plus Personalization Skill Badge, October
  2025.

---

*This document is a reference for humans and tailoring agents. It does not itself
feed the automated pipeline. Update it whenever Casey ships a project or his
schooling record changes, and keep Section 1 in sync with the `Project Stack:`
row and PROJECTS section of `Baseline_Resume.docx`.*
