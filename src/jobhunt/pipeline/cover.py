"""Cover letter pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.models import Job
from jobhunt.pipeline.score import MAX_DESC_CHARS, truncate


@dataclass
class CoverLetter:
    salutation: str
    body: list[str]
    sign_off: str
    model: str

    def to_markdown(self) -> str:
        parts = [self.salutation, ""]
        parts.extend(p.rstrip() + "\n" for p in self.body)
        parts.append(self.sign_off)
        return "\n".join(parts).strip() + "\n"


async def write_cover(cfg: Config, job: Job, *, revisions: str = "") -> CoverLetter:
    if not job.description:
        raise PipelineError(f"job {job.id} has no description")
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    if not verified_path.is_file():
        raise PipelineError(f"missing {verified_path} — run `job-seeker convert-resume`")

    prompt = load_prompt(cfg.paths.kb_dir, "cover")
    user = prompt.render_user(
        verified_facts=verified_path.read_text(encoding="utf-8"),
        title=job.title or "(unknown)",
        company=job.company or "(unknown)",
        location=job.location or "(unknown)",
        description=truncate(job.description, MAX_DESC_CHARS),
        revisions=revisions,
    )
    model = cfg.gateway.tasks.get(prompt.task) or cfg.gateway.tasks["cover"]
    raw = await complete_json(
        base_url=cfg.gateway.base_url,
        model=model,
        system=prompt.system,
        user=user,
        schema=prompt.schema,
        temperature=prompt.temperature,
    )
    body = raw["body"]
    if isinstance(body, str):
        body = [body]
    return CoverLetter(
        salutation=str(raw.get("salutation") or "Dear Hiring Team,"),
        body=[str(p).strip() for p in body],
        sign_off=str(raw.get("sign_off") or "Best,\nCasey Hsu"),
        model=model,
    )


async def write_cover_with_retry(
    cfg: Config,
    job: Job,
    *,
    verified: dict[str, Any],
    company: str | None,
    max_words: int,
    max_attempts: int,
) -> tuple[CoverLetter, list[str], int]:
    """Generate a cover letter, re-running up to max_attempts times when
    `validate_cover` flags violations. Returns (cover, final_violations,
    attempts_used). Falls back to the last attempt after the final retry —
    the caller still gets a draft, and `audit` will mark the verdict `revise`
    so the violations surface to the user.
    """
    # Local import — `cover_validate` imports from this module, so a top-level
    # import would cycle.
    from jobhunt.pipeline.cover_validate import validate_cover

    attempts = max(1, max_attempts)
    last_cover: CoverLetter | None = None
    last_violations: list[str] = []
    revisions = ""
    for attempt in range(1, attempts + 1):
        cover = await write_cover(cfg, job, revisions=revisions)
        violations = validate_cover(
            cover, verified=verified, company=company, max_words=max_words
        )
        if not violations:
            return cover, [], attempt
        last_cover = cover
        last_violations = violations
        revisions = _format_revision_hint(violations, attempt)
    assert last_cover is not None  # loop runs at least once
    return last_cover, last_violations, attempts


def _format_revision_hint(violations: list[str], attempt: int) -> str:
    """Build the {revisions} block injected on the next attempt's prompt.
    Names the specific violations so the model can fix them concretely."""
    lines = ["", "## Previous attempt was rejected by the validator. Fix these:"]
    for v in violations:
        lines.append(f"- {v}")
    lines.append(
        f"Rewrite the letter from scratch. This is retry {attempt + 1}; "
        "do not reuse any phrasing from the prior attempt that triggered a violation."
    )
    return "\n".join(lines)
