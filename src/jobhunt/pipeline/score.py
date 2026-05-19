"""Score a job posting against verified.json. Output matches kb/prompts/score.md schema."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.models import Job
from jobhunt.pipeline._keywords import phrase_present

# Cap inputs to keep prompts within the Ollama server's configured context
# (`OLLAMA_CONTEXT_LENGTH`, currently 16384 tokens). Rule of thumb: ~4 chars/
# token. Combined desc + policy + verified + prompt bodies should leave
# headroom for the model's structured-JSON output. If you change the server
# env, adjust these in step.
MAX_DESC_CHARS = 16000
MAX_POLICY_CHARS = 6000


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
        raise PipelineError(f"missing {verified_path} — run `jobhunt convert-resume` first")

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
    # Adzuna ships ~500-char snippets; the LLM commonly extracts only 1-2 phrases
    # from those, and clamping against a 1/2 denominator over-penalizes signal-poor
    # postings. Skip the clamp when the must-have set is too small to be reliable.
    must_have_count = len(matched) + len(gaps)
    if must_have_count < 3:
        score = raw_score
    else:
        score = _clamp_by_coverage(raw_score, coverage_pct)

    decline_reason = result.get("decline_reason")
    if _is_bogus_senior_decline(decline_reason, job.title or ""):
        decline_reason = None

    # Phase 10.2: Familiar-only-fit cap. When every matched must-have resolves
    # to a skill that's in verified.skills_familiar (Java/Spring Boot/MCP/...
    # — academic / light-use only), the role is at most a stretch — Casey
    # would be misrepresenting himself if the tailored resume claimed Core
    # expertise. Cap the score at 55 and set a decline reason so the role
    # drops out of the default min_score=55 selection band.
    # Reasoning: the May 2026 Java Developer @ Ignite Talent case scored 78
    # (transferable coursework matching let it through) and shipped a
    # Familiar-only-skills resume that misrepresented Casey to any human
    # reviewer. This guard catches that pattern at the score boundary.
    if (
        decline_reason is None
        and matched
        and _all_matched_are_familiar(matched, verified)
    ):
        score = min(score, 54)
        decline_reason = (
            "role's matched skills are all Familiar (academic/light use only); "
            "applying would misrepresent Core production experience"
        )

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
    """True when the LLM declined solely on seniority wording without a real trigger.

    The score prompt (May 2026) is explicit that Senior/Lead/Staff/Principal/Architect
    titles alone are NOT decline triggers — only people-management responsibilities
    in the JD body, or year-thresholds, justify a decline. qwen3.5:9b routinely
    manufactures "Title implies Lead seniority" or "Staff seniority mismatch"
    declines. This guard nullifies a decline when the *only* signal is a
    seniority keyword and the title doesn't actually carry that word AND the
    reason doesn't cite a real trigger (manage / mentor / head of / direct reports).
    """
    if not decline_reason:
        return False
    r = decline_reason.lower()
    seniority_tokens = (
        "senior", "sr.", "seniority", "lead", "staff", "principal", "architect"
    )
    if not any(k in r for k in seniority_tokens):
        return False
    # If the reason cites a real people-management trigger, trust it.
    management_tokens = (
        "manage", "mentor", "direct report", "headcount", "performance review",
        "head of", "people leader"
    )
    if any(k in r for k in management_tokens):
        return False
    # Otherwise check the title. If the title genuinely contains a
    # people-management word (Manager/Director/Head of), keep the decline.
    # Plain Lead/Staff/Principal/Architect/Senior in the title is NOT a trigger
    # by itself — the prompt explicitly allows IC roles with those titles.
    t = (title or "").lower()
    hard_title_triggers = ("manager", "director", "head of", "vp ", "vice president")
    if any(k in t for k in hard_title_triggers):
        return False
    return True


def _all_matched_are_familiar(matched: list[str], verified_blob: str) -> bool:
    """True when every phrase in `matched` resolves into the Familiar bucket
    only (i.e. NOT into skills_core / skills_cms / skills_data_devops /
    skills_ai). Used by the May 2026 Familiar-only-fit cap.

    Word-boundary matching (not substring) — "Java" must NOT match the token
    "JavaScript". If the same phrase is also present in a non-Familiar
    bucket, treat it as Core (the role is salvageable). Conservative —
    err on the side of NOT capping when classification is ambiguous.
    """
    import json as _json
    import re as _re

    try:
        v = _json.loads(verified_blob)
    except (ValueError, TypeError):
        return False
    familiar_items = [s.lower() for s in (v.get("skills_familiar", []) or [])]
    core_items = [
        item.lower()
        for key in ("skills_core", "skills_cms", "skills_data_devops", "skills_ai")
        for item in (v.get(key, []) or [])
    ]
    if not familiar_items:
        return False

    def _in_bucket(phrase: str, items: list[str]) -> bool:
        """Word-boundary match: phrase token-by-token, every token bordered
        by non-word characters in at least one bucket item. 'Java' matches
        'Java' but not 'JavaScript'."""
        tokens = [t for t in _re.findall(r"[a-z0-9+#]+", phrase.lower()) if t]
        if not tokens:
            return False
        for item in items:
            # Build a regex requiring every token to appear as a whole word
            # (or +-#-bounded chunk) within this item.
            if all(_re.search(rf"\b{_re.escape(t)}\b", item) for t in tokens):
                return True
        return False

    for phrase in matched:
        if not _in_bucket(phrase, familiar_items):
            return False
        if _in_bucket(phrase, core_items):
            # Phrase is present in BOTH Familiar and a Core bucket. Treat as
            # Core (the matched item is a legitimate production skill).
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
