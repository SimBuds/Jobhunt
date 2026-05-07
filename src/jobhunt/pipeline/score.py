"""Score a job posting against verified.json. Output matches kb/prompts/score.md schema."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.models import Job
from jobhunt.pipeline._keywords import phrase_present

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
    raw_score = int(result["score"])
    llm_matched = _coerce_phrase_list(result.get("matched_must_haves"))
    llm_gaps = _coerce_phrase_list(result.get("gaps"))

    # Deterministic check: trust the LLM's extraction of which phrases are
    # must-haves (it can read the JD), but verify each against verified.json
    # ourselves. The LLM has been observed listing missing phrases as matched
    # to inflate the score band — this clamp closes that loophole.
    matched, gaps = _verify_against_profile(llm_matched, llm_gaps, verified)
    coverage_pct = _coverage_pct(matched, gaps)
    score = _clamp_by_coverage(raw_score, coverage_pct)

    decline_reason = result.get("decline_reason")
    if _is_bogus_senior_decline(decline_reason, job.title or ""):
        decline_reason = None

    # qwen3.5:9b sometimes uses score=0 as a silent decline (no decline_reason).
    # The prompt forbids this; enforce a floor so non-declined jobs stay in
    # the rubric's 30+ range and remain visible to calibration.
    if decline_reason is None and score < 30:
        score = 40

    return ScoreResult(
        score=score,
        matched_must_haves=matched,
        gaps=gaps,
        decline_reason=decline_reason,
        ai_bonus_present=bool(result.get("ai_bonus_present")),
        model=model,
    )


def _coerce_phrase_list(raw: object) -> list[str]:
    """Schema says items are strings, but qwen sometimes returns dicts. Coerce defensively."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            s = item
        elif isinstance(item, dict):
            s = str(
                item.get("phrase")
                or item.get("name")
                or item.get("skill")
                or item.get("text")
                or item.get("must_have")
                or ""
            )
        else:
            s = str(item) if item is not None else ""
        s = s.strip()
        if s:
            out.append(s)
    return out


def _is_bogus_senior_decline(decline_reason: str | None, title: str) -> bool:
    """True when the LLM declined solely on 'Senior' wording without a real trigger.

    The score prompt is explicit that 'Senior' alone is not a decline trigger,
    but qwen3.5:9b often ignores that rule. This guard nullifies a decline
    when the only signal is the word 'Senior'/'Sr.'/'seniority' and neither
    the reason nor the title cites a genuine trigger (Lead/Principal/etc.).
    """
    if not decline_reason:
        return False
    r = decline_reason.lower()
    if not any(k in r for k in ("senior", "sr.", "seniority")):
        return False
    # The model often pads its reason with a generic laundry list
    # ("Senior/Staff/Lead seniority"), so we don't trust the reason text to
    # tell us whether a real trigger applies. Use the title instead: if the
    # title has no Lead/Principal/Architect/Staff/Manager/Director word,
    # the decline is bogus.
    t = (title or "").lower()
    title_triggers = ("lead", "principal", "architect", "staff", "manager", "director", "head of")
    if any(k in t for k in title_triggers):
        return False
    return True


def _verify_against_profile(
    llm_matched: list[str], llm_gaps: list[str], verified_blob: str
) -> tuple[list[str], list[str]]:
    """Re-partition the LLM's must-have list using the verified profile blob."""
    blob = verified_blob.lower()
    matched: list[str] = []
    gaps: list[str] = []
    seen: set[str] = set()
    for phrase in list(llm_matched) + list(llm_gaps):
        key = phrase.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        (matched if phrase_present(phrase, blob) else gaps).append(phrase)
    return matched, gaps


def _coverage_pct(matched: list[str], gaps: list[str]) -> int:
    total = len(matched) + len(gaps)
    if total == 0:
        return 100
    return round(100 * len(matched) / total)


def _clamp_by_coverage(raw_score: int, coverage_pct: int) -> int:
    """Cap the LLM's score to a band consistent with deterministic coverage.

    Bands (per plan):
      100%       -> keep raw score
      80-99%     -> cap at 89
      60-79%     -> cap at 79
      <60%       -> cap at 64
    """
    if coverage_pct >= 100:
        return raw_score
    if coverage_pct >= 80:
        return min(raw_score, 89)
    if coverage_pct >= 60:
        return min(raw_score, 79)
    return min(raw_score, 64)


def prompt_hash(kb_dir: Path) -> str:
    """Stable hash of the inputs that determine a score, for cache invalidation.

    Covers the score prompt, the candidate's verified facts, and the tailoring
    policy. If any of these change, `scan` re-scores affected jobs.
    """
    import hashlib

    h = hashlib.sha256()
    for rel in ("prompts/score.md", "profile/verified.json", "policies/tailoring-rules.md"):
        p = kb_dir / rel
        if p.is_file():
            h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:16]
