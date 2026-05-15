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
        raise PipelineError(f"missing {verified_path} — run `jobhunt convert-resume` first")

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
    _complete_familiar_bucket(tailored, verified)
    _shrink_to_one_page(tailored)
    return tailored


def _complete_familiar_bucket(tailored: TailoredResume, verified: dict[str, Any]) -> None:
    """Ensure the Familiar category contains every verified `skills_familiar`.

    The tailor prompt requires the Familiar bucket items to be exactly the
    verified set (reorder allowed). The LLM occasionally drops one or two.
    Append any missing items here. The later shrink-to-one-page pass may
    still trim Familiar items down to `_FAMILIAR_FLOOR`, which is intended.
    """
    verified_familiar = list(verified.get("skills_familiar") or [])
    if not verified_familiar:
        return
    for cat in tailored.skills_categories:
        if cat.name.strip().lower() != "familiar":
            continue
        present = {item.strip().lower() for item in cat.items}
        for v in verified_familiar:
            if v.strip().lower() not in present:
                cat.items.append(v)
        return


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


def _try_drop_weakest_bullet(tailored: TailoredResume) -> bool:
    """Drop the last bullet of the role with the highest current line-cost.
    Preserves each role's lead bullet (which the tailor places JD-first).
    Returns True if a bullet was dropped, False if no role has spare bullets.

    May 2026 guard: never trim a role whose `dates` contains "Present" while
    any older role still has > 1 bullet to give. Casey's current contract is
    the most recent, JD-relevant signal — its trailing bullets are the last
    things that should shrink.
    """
    from jobhunt.resume.render_docx import BULLET_CHARS_PER_LINE, _wrapped_lines

    def _is_current(role: TailoredRole) -> bool:
        return "present" in role.dates.lower()

    other_has_spare = any(
        len(r.bullets) > 1 and not _is_current(r) for r in tailored.roles
    )

    worst_role = None
    worst_cost = 0
    for r in tailored.roles:
        if len(r.bullets) <= 1:
            continue
        # Defer current-role trimming while any older role still has slack.
        if _is_current(r) and other_has_spare:
            continue
        cost = sum(_wrapped_lines(b, BULLET_CHARS_PER_LINE) for b in r.bullets)
        if cost > worst_cost:
            worst_cost = cost
            worst_role = r
    if worst_role is None:
        return False
    worst_role.bullets.pop()
    return True


def _shrink_to_one_page(tailored: TailoredResume) -> None:
    """Hard one-page guarantee. Apply trims in order until the resume fits."""
    from jobhunt.resume.render_docx import fits_one_page  # avoid import cycle

    if fits_one_page(tailored):
        return

    sentences = re.split(r"(?<=[.!?])\s+", tailored.summary.strip())
    while len(sentences) > 3 and not fits_one_page(tailored):
        sentences.pop()
        tailored.summary = " ".join(sentences).strip()
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

    # Drop trailing bullets from the heaviest role until we fit or run out.
    # Each role keeps its lead bullet (the JD-relevant one).
    while not fits_one_page(tailored):
        if not _try_drop_weakest_bullet(tailored):
            break
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


def _normalize_aliases(raw: dict[str, Any]) -> dict[str, Any]:
    """Map common qwen3.5:9b alias keys to the schema-correct ones.

    The model often mirrors verified.json's keys (`skills`, `work_history`)
    instead of the schema's (`skills_categories`, `roles`). This best-effort
    rewrite avoids a hard crash; downstream parsing still validates shape.
    """
    out = dict(raw)
    if "skills_categories" not in out and "skills" in out:
        out["skills_categories"] = out["skills"]
    if "roles" not in out and "work_history" in out:
        out["roles"] = out["work_history"]
    return out


def _parse(raw: dict[str, Any], model: str) -> TailoredResume:
    raw = _normalize_aliases(raw)
    try:
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
    except (KeyError, TypeError) as e:
        # qwen3.5:9b sometimes returns a dict that parses as JSON but is
        # missing required keys or has wrong shapes. Convert to a domain
        # error so apply_cmd skips the job instead of crashing.
        raise PipelineError(
            f"tailor returned malformed shape ({type(e).__name__}: {e}); "
            f"keys={sorted(raw.keys())}"
        ) from e


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Tokens that are pure annotation in a skill string — they describe the form
# or surface variation of a verified skill rather than expanding it into a new
# claim. The fabrication check allows a tailored skill to add ONLY these tokens
# on top of a verified skill's token set. Example:
#   verified "Shopify (Liquid)" → tokens {shopify, liquid}
#   tailored "Shopify (Liquid, Custom Themes)" → tokens {shopify, liquid, custom, themes}
#   difference {custom, themes} ⊆ _ANNOTATION_TOKENS → allowed.
# But "React Native" → tokens {react, native}; difference {native} is NOT
# annotation-grade → blocked.
_ANNOTATION_TOKENS: frozenset[str] = frozenset({
    # parenthetical detail words
    "custom", "themes", "certified", "professional", "personalization",
    "skill", "badge", "integration", "integrations", "fundamentals", "advanced",
    # surface-form indicators
    "ci", "cd", "es6", "es2015", "es2020",
    # generic articles / particles that survive tokenisation
    "and", "or", "with", "of", "the",
})


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
            # The tailored skill's tokens MUST be a subset of (or equal to) at
            # least one verified skill's token set. One-way containment only —
            # the previous `or v.issubset(tokens)` branch let "React Native"
            # pass against verified "React" because verified-tokens is a subset
            # of the broader claim. That direction implied Casey owned strict
            # supersets (Native, GraphQL, etc.) of skills he doesn't have.
            #
            # Parenthetical surface-form variation is preserved by `_tokens`
            # (it normalises to alphanumeric tokens), so
            #   "Shopify (Liquid)" verified ⊇ "Shopify (Liquid, Custom Themes)" tailored
            # still passes because the tailored set strictly contains the
            # verified set's distinctive token "liquid" + "shopify" + extras
            # that are mere annotation. To allow that legitimate case while
            # blocking "React Native" against verified "React", require the
            # token-set DIFFERENCE on the tailored side to consist only of
            # annotation-grade tokens (case, custom, themes, certified,
            # professional, integration, etc.).
            if any(tokens == v for v in verified_token_sets):
                pass  # exact match
            elif any(tokens.issubset(v) for v in verified_token_sets):
                pass  # tailored is narrower (e.g. "Postgres" against "PostgreSQL (Postgres)")
            elif any(
                v.issubset(tokens) and (tokens - v).issubset(_ANNOTATION_TOKENS)
                for v in verified_token_sets
            ):
                pass  # tailored adds only annotation (parenthetical detail)
            else:
                raise PipelineError(f"skill not in verified facts: {item!r}")
            if not is_familiar_bucket and any(
                tokens == f or tokens.issubset(f) for f in familiar_token_sets
            ):
                raise PipelineError(f"Familiar skill {item!r} promoted to category {cat.name!r}")
