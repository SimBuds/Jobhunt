---
title: AI Automation and Infrastructure Developer
company: Base Resume — AI Automation Lane
---

We are looking for a developer who builds AI automation and runs the
infrastructure under it: LLM-powered tools and agents that ship to production,
served from self-hosted models on Linux servers the team operates. You will
build the workflows, serve the models, and keep both running.

## What you'll do

- Build LLM-powered tools and agents that plan, call tools, and hand their
  results to people for review before anything acts on them.
- Design prompt pipelines with deterministic validation and retries:
  schema-constrained JSON, post-decode checks, and evals that measure output
  quality on real inputs.
- Self-host LLM inference: serve open-weight models with llama.cpp or a
  similar inference server, manage context windows and GPU memory, and compare
  models on speed and quality before choosing one.
- Run the Linux servers this depends on: systemd services, Docker, reverse
  proxies (nginx or Apache), TLS, backups, and repeatable provisioning scripted
  in Bash.
- Automate end-to-end workflows (ingestion, scoring, content generation,
  browser automation with Playwright) with CI/CD in GitHub Actions.

## Must-haves

- Shipped AI automation built on LLMs, with prompt engineering and evaluation
  that hold up on real inputs.
- Self-hosted model serving: running open-weight models on your own hardware
  or servers, not only calling a hosted API.
- Python for services and tooling, plus JavaScript or TypeScript.
- Linux administration: services, logs, permissions, and Bash scripting.
- Docker, Git, GitHub Actions, REST APIs, and a SQL database such as SQLite.

## Nice-to-haves

- AI coding agents and agent frameworks (Claude Code or similar), tool calling.
- Retrieval pipelines, embeddings, and structured-data (JSON-LD) generation.
- VPS hosting on a cloud provider, and infrastructure defined in code.
- Test automation with Playwright or pytest.
