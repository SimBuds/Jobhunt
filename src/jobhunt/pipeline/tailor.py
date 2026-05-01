"""Tailor pipeline. Produces a structured tailored resume from verified.json + JD."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.models import Job
from jobhunt.pipeline.score import MAX_DESC_CHARS, MAX_POLICY_CHARS, truncate


@dataclass
class TailoredCategory:
    name: str
    items: list[str]


@dataclass
class TailoredRole:
    title: str
    employer: str
    dates: str
    bullets: list[str]


@dataclass
class TailoredResume:
    summary: str
    skills_categories: list[TailoredCategory]
    roles: list[TailoredRole]
    certifications: list[str]
    education: list[str]
    coursework: list[str]
    model: str


async def tailor_resume(cfg: Config, job: Job) -> TailoredResume:
    if not job.description:
        raise PipelineError(f"job {job.id} has no description to tailor against")
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    policy_path = cfg.paths.kb_dir / "policies" / "tailoring-rules.md"
    if not verified_path.is_file():
        raise PipelineError(f"missing {verified_path} — run `job-seeker convert-resume` first")

    verified_text = verified_path.read_text(encoding="utf-8")
    verified = json.loads(verified_text)
    policy = policy_path.read_text(encoding="utf-8") if policy_path.is_file() else ""

    prompt = load_prompt(cfg.paths.kb_dir, "tailor")
    user = prompt.render_user(
        verified_facts=verified_text,
        policy=truncate(policy, MAX_POLICY_CHARS),
        title=job.title or "(unknown)",
        company=job.company or "(unknown)",
        location=job.location or "(unknown)",
        description=truncate(job.description, MAX_DESC_CHARS),
    )
    model = cfg.gateway.tasks.get(prompt.task) or cfg.gateway.tasks["tailor"]
    raw = await complete_json(
        base_url=cfg.gateway.base_url,
        model=model,
        system=prompt.system,
        user=user,
        schema=prompt.schema,
        temperature=prompt.temperature,
    )
    tailored = _parse(raw, model)
    _enforce_no_fabrication(tailored, verified)
    _dedupe_education(tailored)
    _shrink_to_one_page(tailored)
    return tailored


_FAMILIAR_FLOOR = 4


def _dedupe_education(tailored: TailoredResume) -> None:
    """Drop education entries that duplicate the Dean's List / Coursework line.

    `render_docx` always composes a single 'Dean's List (all terms). Coursework: …'
    paragraph from `tailored.coursework`. If the LLM also emits one inside
    `education`, the rendered resume shows the block twice.
    """
    cleaned: list[str] = []
    for line in tailored.education:
        low = line.strip().lower()
        if low.startswith("coursework") or "dean" in low:
            continue
        cleaned.append(line)
    tailored.education = cleaned


def _shrink_to_one_page(tailored: TailoredResume) -> None:
    """Hard one-page guarantee. Apply trims in order until the resume fits."""
    from jobhunt.resume.render_docx import fits_one_page  # avoid import cycle

    if fits_one_page(tailored):
        return

    for cat in tailored.skills_categories:
        if cat.name.strip().lower() != "familiar":
            continue
        while len(cat.items) > _FAMILIAR_FLOOR and not fits_one_page(tailored):
            cat.items.pop()
        break
    if fits_one_page(tailored):
        return

    sentences = re.split(r"(?<=[.!?])\s+", tailored.summary.strip())
    if len(sentences) > 3:
        tailored.summary = " ".join(sentences[:3]).strip()
    if fits_one_page(tailored):
        return

    if tailored.coursework:
        tailored.coursework = []
    if fits_one_page(tailored):
        return

    raise PipelineError(
        "tailored resume still overflows one page after shrink pass; "
        "tighten verified.json bullets or summary"
    )


def _parse(raw: dict[str, Any], model: str) -> TailoredResume:
    return TailoredResume(
        summary=str(raw["summary"]).strip(),
        skills_categories=[
            TailoredCategory(name=c["name"], items=list(c["items"]))
            for c in raw["skills_categories"]
        ],
        roles=[
            TailoredRole(
                title=r["title"],
                employer=r["employer"],
                dates=r["dates"],
                bullets=list(r["bullets"]),
            )
            for r in raw["roles"]
        ],
        certifications=list(raw.get("certifications") or []),
        education=list(raw.get("education") or []),
        coursework=list(raw.get("coursework") or []),
        model=model,
    )


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(s.lower()))


_FORBIDDEN_SENIORITY = ("senior", "sr", "staff", "lead", "principal", "architect")
_CULINARY_TERMS = ("culinary", "chef", "kitchen", "restaurant", "sous")


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _check_summary(summary: str, verified: dict[str, Any]) -> None:
    """Reject summaries that inflate seniority or lead with culinary context."""
    s = summary.lower()
    verified_summary = (verified.get("summary") or "").lower()
    for token in _FORBIDDEN_SENIORITY:
        if _has_word(s, token) and not _has_word(verified_summary, token):
            raise PipelineError(
                f"summary contains seniority token {token!r} not present in verified.summary"
            )
    first_sentence = re.split(r"(?<=[.!?])\s+", summary.strip(), maxsplit=1)[0].lower()
    if any(_has_word(first_sentence, term) for term in _CULINARY_TERMS):
        raise PipelineError(
            "summary leads with culinary/kitchen content; it must come last, not first"
        )


def _enforce_no_fabrication(tailored: TailoredResume, verified: dict[str, Any]) -> None:
    """Hard checks that the tailored output stays inside verified facts."""
    _check_summary(tailored.summary, verified)
    verified_roles = {(r["employer"], r["dates"]) for r in verified.get("work_history", [])}
    tailored_roles = {(r.employer, r.dates) for r in tailored.roles}
    missing = verified_roles - tailored_roles
    extra = tailored_roles - verified_roles
    if missing or extra:
        raise PipelineError(
            f"tailored roles diverged from verified: missing={missing}, extra={extra}"
        )

    verified_skills = [
        s
        for key in (
            "skills_core",
            "skills_cms",
            "skills_data_devops",
            "skills_ai",
            "skills_familiar",
        )
        for s in verified.get(key, [])
    ]
    verified_token_sets = [_tokens(s) for s in verified_skills]
    familiar_token_sets = [_tokens(s) for s in verified.get("skills_familiar", [])]

    for cat in tailored.skills_categories:
        is_familiar_bucket = cat.name.strip().lower() == "familiar"
        for item in cat.items:
            tokens = _tokens(item)
            if not any(tokens.issubset(v) or v.issubset(tokens) for v in verified_token_sets):
                raise PipelineError(f"skill not in verified facts: {item!r}")
            if not is_familiar_bucket and any(
                tokens == f or tokens.issubset(f) for f in familiar_token_sets
            ):
                raise PipelineError(f"Familiar skill {item!r} promoted to category {cat.name!r}")
