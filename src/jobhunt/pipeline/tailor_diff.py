"""Deterministic tailor-diff: surface what changed between baseline
`verified.json` and the LLM's tailored output, without opening the .docx.

Purpose. `apply --top N` produces N tailored resumes — opening each one
to QA is friction. This module generates a markdown diff that highlights
the tailor's decisions: which category became the lead, whether bullets
were reordered or reworded, whether JD-surface-form substitutions fired,
which coursework was surfaced.

No LLM. Pure structural compare. Mirrors the audit philosophy.
"""

from __future__ import annotations

from typing import Any

from jobhunt.pipeline.score import ScoreResult
from jobhunt.pipeline.tailor import TailoredCategory, TailoredResume

_VERIFIED_BUCKETS = (
    ("skills_core", "Core"),
    ("skills_cms", "CMS & E-Commerce"),
    ("skills_data_devops", "Data & DevOps"),
    ("skills_ai", "AI & Tooling"),
    ("skills_familiar", "Familiar"),
)


def _normalize(item: str) -> str:
    """Strip parenthetical annotations + lowercase for matching. Tailor
    rule 9 permits JD-surface-form variants (e.g. 'Postgres' vs the
    verified 'PostgreSQL') — we use casefold + paren-stripped substring
    matches to recognise the same underlying skill."""
    out = item.split("(", 1)[0].strip()
    return out.casefold()


def _build_skill_index(verified: dict[str, Any]) -> dict[str, str]:
    """Map normalized verified-skill → originating bucket name."""
    index: dict[str, str] = {}
    for key, bucket_name in _VERIFIED_BUCKETS:
        for item in verified.get(key, []) or []:
            index[_normalize(item)] = bucket_name
            # Also split on commas inside the value so prose-style AI line
            # ("Ollama, GPU optimization, Claude Code CLI") indexes each
            # token individually.
            for sub in item.split(","):
                sub_norm = _normalize(sub)
                if sub_norm and sub_norm not in index:
                    index[sub_norm] = bucket_name
    return index


def _skills_diff(
    tailored: list[TailoredCategory], skill_index: dict[str, str]
) -> list[str]:
    """Per-category lines describing tailor decisions: category position,
    items, and where each item originated in verified.json."""
    lines: list[str] = ["## Skills"]
    if not tailored:
        lines.append("- (no skills categories emitted)")
        return lines

    lead = tailored[0]
    lines.append(f"- **Lead category:** `{lead.name}` ({len(lead.items)} items)")
    for idx, cat in enumerate(tailored):
        position = "LEAD" if idx == 0 else f"#{idx + 1}"
        lines.append(f"  - {position} `{cat.name}` ({len(cat.items)} items):")
        for item in cat.items:
            origin = skill_index.get(_normalize(item))
            if origin is None:
                lines.append(f"    - `{item}` *(not found in verified — surface form?)*")
            elif origin.lower() != cat.name.casefold() and (
                origin.lower() not in cat.name.casefold()
            ):
                lines.append(f"    - `{item}` (promoted from `{origin}`)")
            else:
                lines.append(f"    - `{item}`")
    return lines


def _roles_diff(
    tailored: TailoredResume, verified: dict[str, Any]
) -> list[str]:
    """Compare bullet ordering and wording vs verified.json baseline."""
    lines: list[str] = ["## Roles"]
    baseline_by_employer: dict[str, dict[str, Any]] = {
        r["employer"]: r for r in (verified.get("work_history") or [])
    }
    for role in tailored.roles:
        base = baseline_by_employer.get(role.employer)
        lines.append(f"### {role.title} | {role.employer} ({role.dates})")
        if base is None:
            lines.append("- *(role not in baseline — should have been blocked)*")
            continue
        base_bullets = list(base.get("bullets") or [])
        lines.append(
            f"- {len(role.bullets)} of {len(base_bullets)} baseline bullets kept"
        )
        for i, bullet in enumerate(role.bullets):
            # Exact substring match against the verified bullet identifies
            # "kept verbatim". Anything else is a reword (still required to
            # describe the same underlying fact per _enforce_no_fabrication).
            verbatim = any(bullet == b for b in base_bullets)
            reordered = False
            if verbatim:
                base_idx = base_bullets.index(bullet)
                if base_idx != i:
                    reordered = True
            tag = "verbatim" if verbatim else "reworded"
            if reordered:
                tag += " + reordered"
            lines.append(f"  {i + 1}. *({tag})* {bullet}")
    return lines


def _coursework_diff(
    tailored_coursework: list[str], verified: dict[str, Any]
) -> list[str]:
    lines: list[str] = ["## Coursework"]
    baseline = verified.get("coursework_baseline") or []
    if not tailored_coursework:
        lines.append("- (no coursework surfaced)")
        return lines
    surfaced_new = [c for c in tailored_coursework if c not in baseline]
    kept = [c for c in tailored_coursework if c in baseline]
    if kept:
        lines.append(f"- {len(kept)} baseline course(s) kept: {', '.join(kept)}")
    if surfaced_new:
        lines.append(
            f"- {len(surfaced_new)} additional course(s) surfaced: {', '.join(surfaced_new)}"
        )
    return lines


def _score_summary(score: ScoreResult | None) -> list[str]:
    if score is None:
        return ["## Score", "- (no score on file)"]
    lines = [
        "## Score",
        f"- score={score.score}",
        f"- matched must-haves: {', '.join(score.matched_must_haves) or '(none)'}",
        f"- gaps: {', '.join(score.gaps) or '(none)'}",
    ]
    return lines


def build_tailor_diff(
    *,
    verified: dict[str, Any],
    tailored: TailoredResume,
    score: ScoreResult | None,
    job_title: str | None = None,
    job_company: str | None = None,
) -> str:
    """Return a markdown summary of tailor decisions. Pure function.

    Wired into `apply_cmd._apply_one` between `write_audit` and the
    `_render_artifacts` step — written to
    `data/applications/<safe_id>/tailor-diff.md`.
    """
    head: list[str] = ["# Tailor diff"]
    if job_title or job_company:
        head.append(f"_{job_title or '?'} @ {job_company or '?'}_")
    head.append(f"_model: {tailored.model}_")
    head.append("")

    skill_index = _build_skill_index(verified)

    sections: list[list[str]] = [
        _score_summary(score),
        ["## Summary", f"> {tailored.summary}"],
        _skills_diff(tailored.skills_categories, skill_index),
        _roles_diff(tailored, verified),
        _coursework_diff(tailored.coursework, verified),
    ]
    body = "\n".join("\n".join(s) for s in sections)
    return "\n".join(head) + "\n" + body + "\n"
