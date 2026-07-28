"""Score a job posting against verified.json. Output matches kb/prompts/score.md schema."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.ingest._filter import is_explicit_junior_title, is_senior_title
from jobhunt.models import Job
from jobhunt.pipeline._keywords import peer_match, phrase_present
from jobhunt.pipeline._profile import candidate_name, render_policy

# Cap inputs to keep prompts within the app-owned context window the gateway
# pins on every call (num_ctx=32768 in gateway.client._DEFAULT_OPTIONS;
# OLLAMA_CONTEXT_LENGTH is deliberately unset on this box). Rule of thumb:
# ~4 chars/token. Combined desc + policy + verified + prompt bodies should
# leave headroom for the model's structured-JSON output. If you change the
# gateway num_ctx, adjust these in step.
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

    # Name the applicant rather than saying "the candidate" — the model grounds
    # measurably better on a concrete referent (IMPLEMENT.md A13). Sourced from
    # the verified profile so the prompt library is not tied to one person.
    name = candidate_name(json.loads(verified))
    prompt = load_prompt(cfg.paths.kb_dir, "score")
    yoe = cfg.applicant.years_experience
    yoe_str = str(yoe) if yoe is not None else "unspecified"
    user = prompt.render_user(
        verified_facts=verified,
        policy=truncate(render_policy(policy, name=name), MAX_POLICY_CHARS),
        years_experience=yoe_str,
        title=job.title or "(unknown)",
        company=job.company or "(unknown)",
        location=job.location or "(unknown)",
        description=truncate(job.description, MAX_DESC_CHARS),
    )
    model = cfg.gateway.tasks.get(prompt.task) or cfg.gateway.tasks["score"]
    result = await complete_json(
        base_url=cfg.gateway.base_url,
        model=model,
        system=prompt.render_system(candidate_name=name),
        user=user,
        schema=prompt.schema,
        temperature=prompt.temperature,
    )
    raw_score = _coerce_score(result.get("score"), job.id)
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
    # postings. Skip the coverage clamp when the must-have set is too small to be
    # reliable.
    must_have_count = len(matched) + len(gaps)
    score = (
        raw_score
        if must_have_count < 3
        else _clamp_by_coverage(raw_score, coverage_pct)
    )

    # Thin-JD confidence ceiling, gated on description LENGTH ALONE.
    #
    # The LLM can't penalize gaps it can't see, so thin snippets float to 82-88
    # and outrank fully-described full-JD roles (audit 2026-05-31: the same
    # ZoomInfo Full Stack Engineer scored 82 from its 500-char Adzuna snippet vs
    # 55 from the 7,140-char Greenhouse JD).
    #
    # This used to sit inside the `must_have_count < 3` branch, which made it a
    # no-op for the postings it most needed to catch: a 500-char snippet is
    # keyword-DENSE, so it routinely yields 4-6 extracted phrases, reaches 100%
    # coverage against them, and sailed past both the coverage clamp and this
    # ceiling. Measured on the 2026-07-28 backlog: 12 of the 13 scores at 78+
    # were 500-char snippets. Phrase count was never the signal — how much JD
    # text the model actually got to read is. Long JDs that merely happened to
    # yield <3 must-haves (e.g. manual `apply --url` fetches) stay exempt,
    # because the gate is length.
    #
    # Applied after whichever clamp ran, and only ever lowers, so the original
    # "don't drag a 1/1 down to 64" intent holds for any score ≤ ceiling.
    if len(job.description) < cfg.pipeline.thin_jd_chars:
        score = min(score, cfg.pipeline.thin_jd_score_cap)

    decline_reason = result.get("decline_reason")

    # Junior-title override (2026-05-22): qwen3.5:9b sometimes emits the
    # YoE-aware "Senior-band title" decline when the JD body uses senior-
    # coded language, even though the posting's title literally says Junior
    # / Intermediate / Mid / Associate / Developer I. Title is the canonical
    # band signal — nullify the decline so these roles stay scoreable.
    if (
        decline_reason
        and "senior-band" in decline_reason.lower()
        and is_explicit_junior_title(job.title)
    ):
        decline_reason = None

    # Senior-band exposure (July 2026): when senior titles are opted in via
    # `applicant.include_senior_roles`, a "Senior-band" decline the model
    # still emits (the prompt now says score 55-70 instead) converts to a
    # confidence ceiling — same posture as thin_jd_score_cap. The role stays
    # applyable in the stretch band without outranking full fits. Only fires
    # when the title actually is senior-band; body-inferred declines on
    # non-senior titles are the junior override's job above.
    if (
        decline_reason
        and "senior-band" in decline_reason.lower()
        and cfg.applicant.include_senior_roles
        and is_senior_title(job.title)
    ):
        decline_reason = None
        score = min(score, 70)

    # Phase 10.2: Familiar-only-fit cap. When every matched must-have resolves
    # to a skill that's in verified.skills_familiar (Java/Spring Boot/MCP/...
    # — academic / light-use only), the role is at most a stretch — the candidate
    # would be misrepresenting himself if the tailored resume claimed Core
    # expertise. Cap the score at 55 and set a decline reason so the role
    # drops out of the default min_score=55 selection band.
    # Reasoning: the May 2026 Java Developer @ Ignite Talent case scored 78
    # (transferable coursework matching let it through) and shipped a
    # Familiar-only-skills resume that misrepresented the candidate to any human
    # reviewer. This guard catches that pattern at the score boundary.
    # July 2026: the prompt now reserves the Familiar-only decline for
    # Senior-band titles, but qwen3.5:9b still emits it on junior/mid
    # postings (it pattern-matches the example string). Nullify those so
    # they fall through to the soft-band cap below.
    if (
        decline_reason
        and "familiar" in decline_reason.lower()
        and not is_senior_title(job.title)
    ):
        decline_reason = None

    if (
        decline_reason is None
        and matched
        and _all_matched_are_familiar(matched, verified)
    ):
        if is_senior_title(job.title):
            # Senior familiar-stack roles stay declined — a Familiar-only
            # resume against a senior bar is a genuine misrepresentation risk
            # (the May 2026 Java Developer @ Ignite Talent ship).
            score = min(score, 54)
            decline_reason = (
                "role's matched skills are all Familiar (academic/light use "
                "only); applying would misrepresent Core production experience"
            )
        else:
            # July 2026 soft band: junior/mid familiar-stack roles are a
            # coachable-junior story (Dean's List coursework + production JS),
            # not a misrepresentation — the resume's Familiar section makes no
            # production claim. Cap into the 55-59 stretch band and keep the
            # role visible instead of declining it.
            score = min(score, 58)

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


def _coerce_score(raw: object, job_id: str) -> int:
    """Schema pins score to integer, but qwen3.5:9b occasionally emits ``null``
    (or a numeric string) despite the grammar. Coerce defensively; an unusable
    score is a model failure for this job, so raise PipelineError — the scan
    loop catches JobHuntError and skips-and-continues, which retries next scan
    rather than inventing a fake number that would pollute calibration."""
    if isinstance(raw, bool):
        # bool is an int subclass; a true/false score is never legitimate.
        raise PipelineError(f"job {job_id}: model returned boolean score {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(float(raw.strip()))
        except ValueError:
            pass
    raise PipelineError(f"job {job_id}: model returned no usable score (got {raw!r})")


def _coerce_phrase_list(raw: object) -> list[str]:
    """Schema says items are strings, but qwen sometimes returns dicts. Coerce defensively.

    The dict key set varies per call (observed live 2026-06-11:
    ``{"requirement": ..., "match_status": ...}`` on one JD,
    ``{"tech": ..., "match_type": ...}`` on the next), so after the known-key
    chain, fall back to the first non-empty string value — JSON object order
    is preserved by the parser and the phrase leads in every observed shape,
    with the match-status vocabulary ("exact", "transferable (...)") second.
    Dropping these items instead used to empty both lists, zero out
    must_have_count, and let raw long-JD scores bypass the coverage clamp.
    """
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
                or next(
                    (v for v in item.values() if isinstance(v, str) and v.strip()),
                    "",
                )
            )
        else:
            s = str(item) if item is not None else ""
        s = s.strip()
        if s:
            out.append(s)
    return out


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
        for key in (
            "skills_core",
            "skills_cms",
            "skills_data_devops",
            "skills_ai",
            "skills_projects",
        )
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
        # Require every token to appear as a whole word, or +-#-bounded chunk,
        # within at least one bucket item.
        return any(
            all(_re.search(rf"\b{_re.escape(t)}\b", item) for t in tokens)
            for item in items
        )

    return all(
        _in_bucket(phrase, familiar_items) and not _in_bucket(phrase, core_items)
        for phrase in matched
    )


# The score prompt instructs the model to annotate transferable matches as
# "Vue (transferable: React)" / "Postgres (transferable: school project —
# SQLite)". This regex pulls out the annotation body so the clamp can verify
# the named bridge against the profile instead of demoting the whole phrase.
_TRANSFER_BRIDGE_RE = re.compile(
    r"\(\s*transferable\b[:\s—–-]*([^)]+)\)", re.IGNORECASE
)


def _bridge_of(phrase: str) -> str | None:
    """Extract the concrete bridge tech from a `(transferable: …)` annotation.

    'Vue (transferable: React)'                          -> 'React'
    'Postgres (transferable: school project — SQLite)'   -> 'SQLite'
    'TypeScript' (no annotation)                          -> None
    """
    m = _TRANSFER_BRIDGE_RE.search(phrase)
    if not m:
        return None
    inner = m.group(1).strip()
    # Prose prefix ("school project — X", "coursework: X") — the concrete
    # tech sits after the last separator.
    for sep in ("—", "–", ":"):
        if sep in inner:
            inner = inner.rsplit(sep, 1)[1].strip()
    return inner or None


def _phrase_verified(phrase: str, blob: str) -> bool:
    """A must-have phrase verifies against the profile when the profile
    literally contains it, contains a peer-family sibling (PEER_FAMILIES —
    the same table the score prompt promises to credit), or contains the
    bridge named by the prompt's `(transferable: X)` annotation. Bogus
    bridges fail closed: the named tech must itself be verified."""
    if phrase_present(phrase, blob) or peer_match(phrase, blob):
        return True
    bridge = _bridge_of(phrase)
    return bridge is not None and phrase_present(bridge, blob)


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
        (matched if _phrase_verified(phrase, blob) else gaps).append(phrase)
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
