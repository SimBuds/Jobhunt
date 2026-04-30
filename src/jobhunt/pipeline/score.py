"""Score a job posting against verified.json. Output matches kb/prompts/score.md schema."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.models import Job

# Cap inputs to keep prompts within the configured num_ctx.
MAX_DESC_CHARS = 6000
MAX_POLICY_CHARS = 4000


def truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit] + "\n[truncated]"


@dataclass
class ScoreResult:
    score: int
    matched_must_haves: list[str]
    gaps: list[str]
    decline_reason: str | None
    ai_bonus_present: bool
    model: str



async def score_job(cfg: Config, job: Job) -> ScoreResult:
    if not job.description:
        raise PipelineError(f"job {job.id} has no description to score")
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    policy_path = cfg.paths.kb_dir / "policies" / "tailoring-rules.md"
    if not verified_path.is_file():
        raise PipelineError(f"missing {verified_path} — run `job-seeker convert-resume` first")

    verified = verified_path.read_text(encoding="utf-8")
    policy = policy_path.read_text(encoding="utf-8") if policy_path.is_file() else ""

    prompt = load_prompt(cfg.paths.kb_dir, "score")
    user = prompt.render_user(
        verified_facts=verified,
        policy=truncate(policy, MAX_POLICY_CHARS),
        title=job.title or "(unknown)",
        company=job.company or "(unknown)",
        location=job.location or "(unknown)",
        description=truncate(job.description, MAX_DESC_CHARS),
    )
    model = cfg.gateway.tasks.get(prompt.task) or cfg.gateway.tasks["score"]
    result = await complete_json(
        base_url=cfg.gateway.base_url,
        model=model,
        system=prompt.system,
        user=user,
        schema=prompt.schema,
        temperature=prompt.temperature,
    )
    return ScoreResult(
        score=int(result["score"]),
        matched_must_haves=list(result.get("matched_must_haves") or []),
        gaps=list(result.get("gaps") or []),
        decline_reason=result.get("decline_reason"),
        ai_bonus_present=bool(result.get("ai_bonus_present")),
        model=model,
    )


def prompt_hash(kb_dir: Path) -> str:
    """Stable hash of the score prompt for cache invalidation."""
    import hashlib

    path = kb_dir / "prompts" / "score.md"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
