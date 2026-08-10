"""Score a job posting against verified.json. Output matches kb/prompts/score.md schema."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.ingest._filter import is_explicit_junior_title, is_senior_title
from jobhunt.models import Job
from jobhunt.pipeline._keywords import peer_match, phrase_present
from jobhunt.pipeline._profile import candidate_name, render_policy

# Cap inputs to keep prompts within the app-owned context window the gateway
# pins on every call (num_ctx=32768 in gateway.client._DEFAULT_OPTIONS;
# OLLAMA_CONTEXT_LENGTH is deliberately unset on this box). If you change the
# gateway num_ctx, adjust these in step — see the sizing note there.
#
# Do NOT size this by a chars/token rule of thumb. Measured `prompt_eval_count`
# on the longest JD in the backlog runs ~23% above a chars/4 estimate, because
# dense JD text tokenizes worse than prose. At 16000 chars the tailor prompt
# measures 11886 tokens; with num_predict=4096 that is 15982, which fits 32768
# comfortably but left only 402 tokens at the 16384 window trialled on
# 2026-07-28. Overflow is silent: Ollama truncates the prompt, the schema
# instruction falls off the end, and the model emits prose instead of JSON.
#
# Held at 16000 rather than lowered: 16000 truncates 9% of the backlog, 10000
# truncates 19%. A trailing "Preferred qualifications" block is real tier-2
# scoring signal, so the shorter cap costs measurable fit accuracy.
MAX_DESC_CHARS = 16000
MAX_POLICY_CHARS = 6000

# --- Score model (2026-07-28) ----------------------------------------------
#
# The LLM no longer picks the number. It extracts the posting's requirements
# into two tiers and annotates transferable bridges; the score is computed
# here. The reason is measured, not stylistic: across 169 live scores, six
# integers accounted for 136 of them and nothing ever exceeded 82. A 9B model
# at temperature 0 asked to choose from prose bands collapses onto the band
# midpoints, and the old rubric's "vary the score across jobs" instruction
# could never work because each job is scored in its own call — the model
# never sees a batch to vary within.
#
# Splitting hard requirements from wish-list items is the substantive fix.
# The old coverage clamp divided by an unweighted phrase list, so a posting
# whose core stack the candidate fully matched but whose nice-to-haves he
# missed landed at 55-70% "coverage" and got capped into the low 60s — the
# exact band the user reported for roles that actually produced interviews.
#
# These mirror the `[pipeline] score_*` config defaults and exist so the module
# is usable (and testable) without constructing a Config. `ScoreWeights.
# from_config` is what the live path uses. Keep the arithmetic a pure function
# of the extraction plus these weights, so a score can always be explained.
SCORE_BASE = 30
SCORE_TIER1_WEIGHT = 50
SCORE_TIER2_WEIGHT = 10
SCORE_AI_BONUS = 5
# A peer-family or annotated-bridge match is real but weaker evidence than the
# literal tech. Grading it below 1.0 is what lets an exact-stack fit outrank a
# bridged one instead of tying with it.
SCORE_TRANSFERABLE_CREDIT = 0.7
# Band separation. See the `[pipeline] senior_score_cap` / `junior_score_bonus`
# notes in config.py for why the previous senior ceiling never fired.
SCORE_SENIOR_CAP = 60
SCORE_JUNIOR_BONUS = 5


@dataclass(frozen=True)
class ScoreWeights:
    """The score model's tunable coefficients, resolved once per `score_job`.

    Passed explicitly rather than read from module globals so a score is
    reproducible from its inputs alone, and so tests can vary one coefficient
    without monkeypatching module state.
    """

    base: int = SCORE_BASE
    tier1: int = SCORE_TIER1_WEIGHT
    tier2: int = SCORE_TIER2_WEIGHT
    ai_bonus: int = SCORE_AI_BONUS
    transferable_credit: float = SCORE_TRANSFERABLE_CREDIT
    senior_cap: int = SCORE_SENIOR_CAP
    junior_bonus: int = SCORE_JUNIOR_BONUS

    @classmethod
    def from_config(cls, cfg: Config) -> ScoreWeights:
        p = cfg.pipeline
        return cls(
            base=p.score_base,
            tier1=p.score_tier1_weight,
            tier2=p.score_tier2_weight,
            ai_bonus=p.score_ai_bonus,
            transferable_credit=p.score_transferable_credit,
            senior_cap=p.senior_score_cap,
            junior_bonus=p.junior_score_bonus,
        )


DEFAULT_WEIGHTS = ScoreWeights()


@dataclass(frozen=True)
class ScoreBreakdown:
    """How one score was reached, persisted to `scores.breakdown`.

    Exists so weight tuning can be driven by interview outcomes. The final
    integer alone is ambiguous: two jobs at 70 may be a full-coverage snippet
    pulled down by the thin-JD ceiling and a genuine two-thirds match, which
    are not the same bet. `caps_applied` records which ceilings actually bound,
    so a capped score is never mistaken for an earned one.
    """

    tier1_matched: int
    tier1_total: int
    tier1_credit: float
    tier2_matched: int
    tier2_total: int
    tier2_credit: float
    ai_bonus: bool
    computed: int          # before any cap
    final: int             # after every cap
    caps_applied: list[str]
    weights: dict[str, float]

    def to_json(self) -> str:
        import json as _json

        return _json.dumps(
            {
                "tier1": {
                    "matched": self.tier1_matched,
                    "total": self.tier1_total,
                    "credit": round(self.tier1_credit, 4),
                },
                "tier2": {
                    "matched": self.tier2_matched,
                    "total": self.tier2_total,
                    "credit": round(self.tier2_credit, 4),
                },
                "ai_bonus": self.ai_bonus,
                "computed": self.computed,
                "final": self.final,
                "caps_applied": list(self.caps_applied),
                "weights": self.weights,
            },
            sort_keys=True,
        )

    @property
    def tier1_coverage(self) -> float:
        """Graded tier-1 coverage in 0.0-1.0, or 0.0 when nothing was extracted.

        This is the number worth calibrating against: it reflects fit to the
        posting's hard requirements, independent of which ceilings happened to
        bind afterwards."""
        return self.tier1_credit / self.tier1_total if self.tier1_total else 0.0


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
    # How the score was reached. Defaulted + last so existing construction
    # (tests, `apply_cmd._load_score` rebuilding from the DB) stays valid, and
    # so a row scored before the breakdown column existed reads back as None
    # rather than as a fabricated zero.
    breakdown: ScoreBreakdown | None = None


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
    decline_reason = result.get("decline_reason")
    tier1_phrases = _coerce_phrase_list(result.get("must_haves"))
    tier2_phrases = _coerce_phrase_list(result.get("nice_to_haves"))

    # A posting with no extractable requirement at all is a model failure, not
    # a zero-fit job — same posture the old unusable-score path took. `scan`
    # catches JobHuntError per job and skips it with a message, so this
    # surfaces instead of silently scoring every such posting at the base.
    #
    # Declines are exempt: the model may legitimately stop reading once it
    # decides the title is people-management, and raising there would mean the
    # decline never persists and the job is re-scored on every scan forever.
    if not tier1_phrases and not tier2_phrases and not decline_reason:
        raise PipelineError(
            f"job {job.id}: model extracted no requirements from the posting"
        )
    # A posting whose requirements all read as optional still has a real bar.
    # Promote rather than score it out of the queue on a phrasing quirk.
    if not tier1_phrases:
        tier1_phrases, tier2_phrases = tier2_phrases, []

    # Trust the LLM's reading of WHICH phrases the posting requires and at what
    # tier — that needs the JD text. Do not trust its claim that the candidate
    # has them: every phrase is re-verified against verified.json here, so a
    # hallucinated match becomes a gap and lowers the score instead of raising
    # it. `seen` is shared so a phrase repeated across tiers counts once.
    weights = ScoreWeights.from_config(cfg)
    seen: set[str] = set()
    tier1 = _verify_tier(tier1_phrases, verified.lower(), seen, weights)
    tier2 = _verify_tier(tier2_phrases, verified.lower(), seen, weights)

    matched = tier1.matched + tier2.matched
    gaps = tier1.gaps + tier2.gaps
    ai_bonus = bool(result.get("ai_bonus_present"))
    junior_bonus = is_explicit_junior_title(job.title)
    computed = _compute_score(tier1, tier2, ai_bonus, weights, junior_bonus)
    score = computed
    # Which ceilings actually bound, recorded as they fire. A capped score must
    # never be mistaken for an earned one during calibration.
    caps_applied: list[str] = []

    # Thin-JD confidence ceiling, gated on description LENGTH ALONE.
    #
    # The LLM can't penalize gaps it can't see, so thin snippets float to 82-88
    # and outrank fully-described full-JD roles (audit 2026-05-31: the same
    # ZoomInfo Full Stack Engineer scored 82 from its 500-char Adzuna snippet vs
    # 55 from the 7,140-char Greenhouse JD).
    #
    # This used to be gated on a small extracted-phrase count, which made it a
    # no-op for the postings it most needed to catch: a 500-char snippet is
    # keyword-DENSE, so it yields 4-6 phrases at full coverage and looked like
    # a confident match. Measured on the 2026-07-28 backlog: 12 of the 13
    # scores at 78+ were 500-char snippets. Phrase count was never the signal —
    # how much JD text the model actually got to read is.
    #
    # It matters just as much under tier-based scoring: a snippet lists a few
    # technologies with no Requirements/Preferred structure, so everything
    # lands in tier-1 and a candidate who matches those few keywords reaches
    # near-full tier-1 coverage against a bar the posting never really stated.
    # Full-length JDs are exempt regardless of how few requirements they
    # yielded, because the gate is length.
    #
    # Only ever lowers.
    if len(job.description) < cfg.pipeline.thin_jd_chars:
        if cfg.pipeline.thin_jd_score_cap < score:
            caps_applied.append("thin_jd")
        score = min(score, cfg.pipeline.thin_jd_score_cap)

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

    # Senior-band ceiling, gated on TITLE ALONE (2026-08-10).
    #
    # This used to require the model to emit a "Senior-band" decline_reason,
    # which the July 2026 prompt rewrite stopped it from doing — the prompt
    # now says senior titles are "extracted normally" because "a deterministic
    # ceiling downstream already keeps them from outranking full fits". That
    # ceiling was unreachable: across the 650-score backlog it fired 0 times
    # on 62 undeclined senior-titled roles, so senior postings carried a
    # higher median (60) than explicit junior/mid ones (50).
    #
    # Now unconditional on senior titles. A model-emitted "Senior-band"
    # decline is still nullified when senior roles are opted in, so the role
    # stays applyable in the stretch band rather than vanishing.
    #
    # Only ever lowers.
    if is_senior_title(job.title):
        if (
            decline_reason
            and "senior-band" in decline_reason.lower()
            and cfg.applicant.include_senior_roles
        ):
            decline_reason = None
        if weights.senior_cap < score:
            caps_applied.append("senior_band")
        score = min(score, weights.senior_cap)

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
            if score > 54:
                caps_applied.append("familiar_only_senior")
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
            if score > 58:
                caps_applied.append("familiar_only_junior")
            score = min(score, 58)

    # The old score=0-as-silent-decline floor bump is gone: the model no longer
    # emits a number, and SCORE_BASE is itself the floor for anything that
    # isn't declined. Every cap above only lowers toward it, never past it.

    return ScoreResult(
        score=score,
        matched_must_haves=matched,
        gaps=gaps,
        decline_reason=decline_reason,
        ai_bonus_present=ai_bonus,
        model=model,
        breakdown=ScoreBreakdown(
            tier1_matched=len(tier1.matched),
            tier1_total=tier1.total,
            tier1_credit=tier1.credit,
            tier2_matched=len(tier2.matched),
            tier2_total=tier2.total,
            tier2_credit=tier2.credit,
            ai_bonus=ai_bonus,
            computed=computed,
            final=score,
            caps_applied=caps_applied,
            weights={
                "base": weights.base,
                "tier1": weights.tier1,
                "tier2": weights.tier2,
                "ai_bonus": weights.ai_bonus,
                "transferable_credit": weights.transferable_credit,
                "senior_cap": weights.senior_cap,
                "junior_bonus": weights.junior_bonus,
            },
        ),
    )


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


def _phrase_credit(
    phrase: str, blob: str, weights: ScoreWeights = DEFAULT_WEIGHTS
) -> float:
    """Graded evidence that the profile satisfies one JD requirement.

    1.0 for a literal hit, `weights.transferable_credit` for a peer-family or
    annotated-bridge hit, 0.0 for no path at all. Three acceptance routes in a
    fixed order, fail-closed on bogus bridges (the named tech must itself
    verify), returning strength rather than a boolean so an exact stack match
    can outrank a bridged one.
    """
    if phrase_present(phrase, blob):
        return 1.0
    if peer_match(phrase, blob):
        return weights.transferable_credit
    bridge = _bridge_of(phrase)
    if bridge is not None and phrase_present(bridge, blob):
        return weights.transferable_credit
    return 0.0


@dataclass(frozen=True)
class _TierResult:
    """One tier's verification outcome. `credit` is the graded sum, so it can
    be below `len(matched)` when matches came through bridges."""

    matched: list[str]
    gaps: list[str]
    credit: float

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.gaps)

    @property
    def coverage(self) -> float:
        """Graded coverage in 0.0-1.0. An empty tier scores 0.0; callers decide
        what an empty tier means rather than inheriting a silent 100%."""
        return self.credit / self.total if self.total else 0.0


def _verify_tier(
    phrases: list[str],
    blob: str,
    seen: set[str],
    weights: ScoreWeights = DEFAULT_WEIGHTS,
) -> _TierResult:
    """Partition one tier's phrases into matched/gaps with graded credit.

    `seen` is shared across tiers so a requirement the model listed in both
    lists is counted once, in the tier it appeared in first (tier-1 is
    verified first, which is the conservative direction: it keeps a genuine
    hard requirement from being demoted to a wish-list item).
    """
    matched: list[str] = []
    gaps: list[str] = []
    credit = 0.0
    for phrase in phrases:
        key = phrase.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        c = _phrase_credit(phrase, blob, weights)
        if c > 0:
            matched.append(phrase)
            credit += c
        else:
            gaps.append(phrase)
    return _TierResult(matched=matched, gaps=gaps, credit=credit)


def _compute_score(
    tier1: _TierResult,
    tier2: _TierResult,
    ai_bonus: bool,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
    junior_bonus: bool = False,
) -> int:
    """Deterministic score from graded tier coverage.

    base + tier1_weight * tier1_coverage + tier2_weight * tier2_coverage
         + ai_bonus + junior_bonus

    An empty tier-2 folds its weight into tier-1 rather than awarding free
    points. Short postings frequently state hard requirements and no wish
    list at all, and a posting should not score higher merely because it
    forgot to write one.

    `junior_bonus` lifts explicitly junior/mid-titled postings — the band
    actually worth chasing at this YoE — so they outrank senior stretch roles
    at equal coverage. Added here rather than after the caps so a thin JD or a
    Familiar-only fit cannot ride the bonus past its ceiling.
    """
    t1_weight = weights.tier1
    t2_weight = weights.tier2
    if tier2.total == 0:
        t1_weight += t2_weight
        t2_weight = 0

    score = weights.base + t1_weight * tier1.coverage + t2_weight * tier2.coverage
    if ai_bonus:
        score += weights.ai_bonus
    if junior_bonus:
        score += weights.junior_bonus
    return round(score)


def prompt_hash(cfg: Config) -> str:
    """Stable hash of the inputs that determine a score, for cache invalidation.

    Covers the score prompt, the candidate's verified facts, the tailoring
    policy, and the score weights. If any of these change, `scan` re-scores
    affected jobs.

    Takes the whole `Config` rather than a `kb_dir` so the weights cannot be
    left out at a call site: a weight change that did not move the hash would
    silently leave a backlog of scores computed under the old coefficients,
    mixed in with new ones and indistinguishable from them.
    """
    import hashlib

    h = hashlib.sha256()
    for rel in ("prompts/score.md", "profile/verified.json", "policies/tailoring-rules.md"):
        p = cfg.paths.kb_dir / rel
        if p.is_file():
            h.update(p.read_bytes())
        h.update(b"\0")
    w = ScoreWeights.from_config(cfg)
    # Canonical, order-stable, and explicit about the float so a 0.7 -> 0.70
    # reformat cannot change the digest on its own.
    h.update(
        f"base={w.base};t1={w.tier1};t2={w.tier2};"
        f"ai={w.ai_bonus};transfer={w.transferable_credit:.6f};"
        f"senior_cap={w.senior_cap};junior_bonus={w.junior_bonus}".encode()
    )
    return h.hexdigest()[:16]
