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
    """Single-attempt tailor. Preserved as the original entry point — tests
    and callers that don't need retry behaviour use this directly.

    The retry-aware caller (`apply_cmd._apply_one`) uses
    `tailor_resume_with_retry` instead; on a `FabricationError`, it re-prompts
    with a corrective hint up to `cfg.pipeline.tailor_retry_attempts` times.
    """
    return await _tailor_once(cfg, job, revisions="")


async def _tailor_once(cfg: Config, job: Job, *, revisions: str) -> TailoredResume:
    """One pass through the tailor LLM + post-processing checks. Raises
    `FabricationError` (a `PipelineError`) on any fabrication violation;
    the caller decides whether to retry or surface."""
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
    # Retry attempts force temperature to 0 so qwen deterministically obeys
    # the correction hint ("REMOVE 'Redux'") rather than re-sampling the same
    # JD-mirrored skill on attempt 2/3. Phase 9 observation: Targeted Talent
    # JDs that explicitly require Redux had qwen producing Redux on all 3
    # attempts at temp=0.3 despite the hint. Dropping to temp=0 on retry
    # closes that loop. The first attempt keeps the frontmatter temperature
    # (0.3) so legitimate creative tailoring isn't punished.
    if revisions:
        # Append the correction hint at the end of the user prompt; the model
        # sees the same JD context plus the violation list to address.
        user = f"{user}\n{revisions}"
        temperature = 0.0
    else:
        temperature = prompt.temperature
    model = cfg.gateway.tasks.get(prompt.task) or cfg.gateway.tasks["tailor"]
    raw = await complete_json(
        base_url=cfg.gateway.base_url,
        model=model,
        system=prompt.system,
        user=user,
        schema=prompt.schema,
        temperature=temperature,
    )
    tailored = _parse(raw, model)
    _enforce_no_fabrication(tailored, verified)
    _dedupe_education(tailored)
    _complete_familiar_bucket(tailored, verified)
    _cap_lead_category_size(tailored)
    _shrink_to_one_page(tailored)
    return tailored


async def tailor_resume_with_retry(
    cfg: Config,
    job: Job,
    *,
    max_attempts: int = 3,
) -> tuple[TailoredResume, list["FabricationViolation"], int]:
    """Tailor a resume with deterministic retry on fabrication violations.

    Mirrors `pipeline.cover.write_cover_with_retry`. On each attempt:
      1. Call `_tailor_once` with the accumulated revision hint.
      2. If it returns cleanly, return the tailored resume.
      3. If it raises `FabricationError`, capture violations, build a hint,
         retry — up to `max_attempts` times.
      4. After the final failed attempt, re-raise the last `FabricationError`
         so the caller (apply_cmd) surfaces it and skips the job. This
         matches today's UX — no fabrication ever ships unblocked.

    Returns `(tailored, [], attempts_used)` on success. The empty-violations
    list mirrors the cover-retry signature so call-site logging stays uniform.
    """
    attempts = max(1, max_attempts)
    last_violations: list[FabricationViolation] = []
    last_error: FabricationError | None = None
    revisions = ""
    for attempt in range(1, attempts + 1):
        try:
            tailored = await _tailor_once(cfg, job, revisions=revisions)
        except FabricationError as e:
            last_violations = list(e.violations)
            last_error = e
            revisions = _format_tailor_revision_hint(last_violations, attempt)
            continue
        return tailored, [], attempt
    # Final attempt failed — re-raise the last error so apply_cmd skips this
    # job cleanly. Never ship an unverified resume; retry is recovery only.
    assert last_error is not None
    raise last_error


def _format_tailor_revision_hint(
    violations: list["FabricationViolation"], attempt: int
) -> str:
    """Build the `{revisions}` block injected at the end of the next attempt's
    user prompt. Names each violation concretely so the model can fix it
    rather than re-guessing. Mirrors `cover._format_revision_hint`."""
    lines = ["", "## Previous attempt was rejected by the fabrication check. Fix these:"]
    for v in violations:
        lines.append(_violation_hint_line(v))
    lines.append(
        f"Rewrite the resume from scratch. This is retry {attempt + 1}; "
        "do not reuse skills_categories items, summary phrasing, or role "
        "details from the prior attempt that triggered a violation."
    )
    return "\n".join(lines)


def _violation_hint_line(v: "FabricationViolation") -> str:
    """One bullet describing how to remediate a single violation. Kind-driven
    so the model gets the right corrective rule, not a generic 'try again'."""
    if v.kind == "unverified-skill":
        return (
            f"- The skill {v.detail!r} is NOT in verified.json. REMOVE it "
            "entirely from skills_categories — do NOT replace it with another "
            "unverified skill. If the JD asks for it, leave it as a known gap."
        )
    if v.kind == "familiar-promoted":
        return (
            f"- The skill {v.detail!r} is Familiar (academic / light use). It "
            "must appear ONLY in a category named exactly 'Familiar', or be "
            "removed entirely. Do not place it in any other category even if "
            "the JD asks for it."
        )
    if v.kind == "role-divergence":
        return (
            f"- Tailored roles diverged from verified work_history ({v.detail}). "
            "The `roles` array MUST contain every verified role with the exact "
            "same `employer` and `dates` — no invented roles, no missing ones."
        )
    if v.kind == "summary-seniority":
        return (
            f"- The summary contains seniority token {v.detail!r} that is not "
            "in verified_facts.summary. Remove the seniority qualifier — "
            "Casey is an IC engineer; do not prepend Senior/Lead/Staff/etc."
        )
    if v.kind == "summary-culinary":
        return (
            "- The summary leads with culinary/kitchen content. Move any "
            "culinary clause to the LAST sentence only, and only if the JD "
            "explicitly asks for team-management or operational leadership."
        )
    return f"- Unspecified violation ({v.kind}): {v.detail}"


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

# Phase 9 enforcement: tailor.md rule 10 caps the first skills category at
# 6-10 items, but qwen3.5:9b obeyed it only ~38% of the time in live runs
# (5/8 outputs landed 11-12 items in the lead category). This post-process
# trims any overflow deterministically, mirroring the shrink-ladder pattern.
_LEAD_CATEGORY_MAX = 10


def _cap_lead_category_size(tailored: TailoredResume) -> None:
    """Cap the first skills category at `_LEAD_CATEGORY_MAX` items.

    Overflow (anything beyond item 10) is appended to the SECOND category
    rather than dropped — the items are still verified skills, just not
    JD-primary. The `Familiar` bucket is never used as overflow target;
    Familiar items must come from verified.skills_familiar only.

    No-op when:
    - the lead category is already at-or-under the cap;
    - there's only one non-Familiar category and the lead overflows (we
      append to that lone bucket's tail-extension instead — see below);
    - skills_categories is empty.

    Edge case: when the lead overflows but every other category is
    "Familiar", we create a new category named "Additional" right before
    Familiar to receive the overflow. This is rare; most tailored outputs
    have 2-4 non-Familiar categories.
    """
    if not tailored.skills_categories:
        return
    lead = tailored.skills_categories[0]
    if len(lead.items) <= _LEAD_CATEGORY_MAX:
        return

    overflow = lead.items[_LEAD_CATEGORY_MAX:]
    lead.items = lead.items[:_LEAD_CATEGORY_MAX]

    # Find the first non-Familiar secondary category to receive the overflow.
    target = None
    for cat in tailored.skills_categories[1:]:
        if cat.name.strip().lower() != "familiar":
            target = cat
            break

    if target is not None:
        # Prepend overflow (keeps tail of secondary intact, surfaces overflow
        # higher in the secondary's own ranking which mirrors lead priority).
        target.items = overflow + target.items
        return

    # No non-Familiar secondary exists. Create an "Additional" bucket before
    # Familiar (or at the end if Familiar isn't present either).
    additional = TailoredCategory(name="Additional", items=overflow)
    familiar_index = next(
        (
            i
            for i, c in enumerate(tailored.skills_categories)
            if c.name.strip().lower() == "familiar"
        ),
        None,
    )
    if familiar_index is None:
        tailored.skills_categories.append(additional)
    else:
        tailored.skills_categories.insert(familiar_index, additional)


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

# Tokens that are pure annotation / decoration in a skill string. They name
# a category, modifier, or surface-form variation, not an identity claim.
# These are stripped from BOTH the tailored and verified sides before
# fabrication comparison, so legitimate JD-surface variants pass while
# superset claims like "React Native" still get rejected.
#
# Includes the JD-surface tokens from tailor.md rule 9's normalization table
# (headless, cms, rest, api, apis) since those are category labels the LLM
# is explicitly instructed to wrap verified anchors in.
_ANNOTATION_TOKENS: frozenset[str] = frozenset({
    # parenthetical detail words
    "custom", "themes", "certified", "professional", "personalization",
    "skill", "badge", "integration", "integrations", "fundamentals", "advanced",
    # surface-form / category indicators
    "ci", "cd", "es6", "es2015", "es2020",
    # Category wrappers — these decorate an anchor but don't identify it.
    # "headless CMS (Contentful)" → identity {contentful}; "headless CMS" alone
    # → empty identity → rejected (too vague).
    "headless", "cms", "api", "apis",
    # generic articles / particles that survive tokenisation
    "and", "or", "with", "of", "the",
})

# Short-form JD aliases that should normalise to their canonical token before
# comparison. Mirrors tailor.md rule 9. When the LLM (correctly) writes the
# JD's short form like "JS" or "GH Actions", we map it back to the canonical
# token ("javascript", "github") so set-comparison still recognises it as a
# match against verified.json's long form.
#
# Note: "rest" and "restful" both stay as identity tokens (NOT annotation), so
# a tailored "REST APIs" still has identity {restful} after alias normalisation
# and matches verified "RESTful APIs" cleanly. Without this, both reduce to
# empty identity and the fabrication check loses signal.
_SURFACE_ALIASES: dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "gh": "github",
    "postgres": "postgresql",
    "rest": "restful",
    "node": "nodejs",   # verified has "Node.js" → tokenises to {node, js}; we
                        # collapse both sides via this alias + the "js" mapping
                        # for safety
    "nextjs": "next",   # rare; the tokenizer drops the dot in "Next.js"
}


def _tokens(s: str) -> frozenset[str]:
    raw = _TOKEN_RE.findall(s.lower())
    return frozenset(_SURFACE_ALIASES.get(t, t) for t in raw)


def _identity_tokens(s: str) -> frozenset[str]:
    """Tokens with annotation/decoration stripped — the load-bearing identity
    of a skill claim. Used by the fabrication check so JD-surface variants
    pass cleanly while superset claims still get rejected.

    Example:
      "Contentful (Certified Professional)" → raw {contentful, certified,
        professional} → identity {contentful}
      "headless CMS (Contentful)"           → raw {headless, cms, contentful}
                                            → identity {contentful}
      Both share the same identity → match.

      "React Native" → raw {react, native} → identity {react, native}
      "React"        → raw {react}         → identity {react}
      Identities differ; "React Native" is NOT a subset of "React" → blocked.
    """
    return _tokens(s) - _ANNOTATION_TOKENS


_FORBIDDEN_SENIORITY = ("senior", "sr", "staff", "lead", "principal", "architect")
_CULINARY_TERMS = ("culinary", "chef", "kitchen", "restaurant", "sous")


@dataclass(frozen=True)
class FabricationViolation:
    """One specific way the tailored output diverged from verified facts.

    `kind` is a stable identifier used by the retry-hint formatter to choose
    the right corrective instruction. `detail` carries the offending substring
    (skill name, summary token, role tuple repr).
    """

    kind: str   # unverified-skill | familiar-promoted | role-divergence | summary-seniority | summary-culinary
    detail: str


class FabricationError(PipelineError):
    """Raised by `_enforce_no_fabrication` with structured violations.

    Subclasses `PipelineError` so existing `except PipelineError` callers
    (audit, apply_cmd, tests) keep working unchanged. The `violations`
    attribute is consumed by `tailor_resume_with_retry` to build a
    deterministic correction hint for the next attempt.

    The exception's `str()` form mirrors the legacy single-message format
    (first violation's human-readable text) so tests that match against
    `PipelineError(match="not in verified")` still pass.
    """

    def __init__(self, violations: list[FabricationViolation], message: str):
        super().__init__(message)
        self.violations = violations


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _check_summary(summary: str, verified: dict[str, Any]) -> None:
    """Reject summaries that inflate seniority or lead with culinary context.

    Raises FabricationError with a single violation. First-violation-wins is
    the legacy behaviour; we don't try to collect every summary issue.
    """
    s = summary.lower()
    verified_summary = (verified.get("summary") or "").lower()
    for token in _FORBIDDEN_SENIORITY:
        if _has_word(s, token) and not _has_word(verified_summary, token):
            msg = f"summary contains seniority token {token!r} not present in verified.summary"
            raise FabricationError(
                [FabricationViolation("summary-seniority", token)], msg
            )
    first_sentence = re.split(r"(?<=[.!?])\s+", summary.strip(), maxsplit=1)[0].lower()
    if any(_has_word(first_sentence, term) for term in _CULINARY_TERMS):
        msg = "summary leads with culinary/kitchen content; it must come last, not first"
        raise FabricationError(
            [FabricationViolation("summary-culinary", "first-sentence")], msg
        )


def _enforce_no_fabrication(tailored: TailoredResume, verified: dict[str, Any]) -> None:
    """Hard checks that the tailored output stays inside verified facts.

    Raises `FabricationError` (a `PipelineError` subclass) on the first
    violation with structured `violations` for the retry layer. First-fail
    semantics preserved — does NOT enumerate all violations to avoid changing
    audit/apply behaviour for callers that just match the exception message.
    """
    _check_summary(tailored.summary, verified)
    verified_roles = {(r["employer"], r["dates"]) for r in verified.get("work_history", [])}
    tailored_roles = {(r.employer, r.dates) for r in tailored.roles}
    missing = verified_roles - tailored_roles
    extra = tailored_roles - verified_roles
    if missing or extra:
        msg = f"tailored roles diverged from verified: missing={missing}, extra={extra}"
        # detail encodes the divergence — extra is the more actionable side
        # for an LLM ("you invented this role"), so prefer it when present.
        detail = f"extra={sorted(extra)}" if extra else f"missing={sorted(missing)}"
        raise FabricationError(
            [FabricationViolation("role-divergence", detail)], msg
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
    verified_identity_sets = [_identity_tokens(s) for s in verified_skills]
    familiar_identity_sets = [_identity_tokens(s) for s in verified.get("skills_familiar", [])]

    for cat in tailored.skills_categories:
        is_familiar_bucket = cat.name.strip().lower() == "familiar"
        for item in cat.items:
            tailored_identity = _identity_tokens(item)
            # Strip annotations from both sides, then require the tailored's
            # identity tokens to be a subset of some verified skill's identity.
            # This handles JD-surface variants ("headless CMS (Contentful)"
            # against "Contentful (Certified Professional)" — both have
            # identity {contentful}) while still blocking superset claims
            # like "React Native" against verified "React" (identities
            # differ; the broader claim is not a subset of the narrower fact).
            #
            # An empty tailored identity (pure annotation, like just "REST APIs"
            # with no anchor) only passes when verified has an empty-identity
            # skill too — vanishingly rare; the check is still safe.
            if not any(
                tailored_identity.issubset(v) and v  # v must be non-empty
                for v in verified_identity_sets
            ):
                msg = f"skill not in verified facts: {item!r}"
                raise FabricationError(
                    [FabricationViolation("unverified-skill", item)], msg
                )
            # Familiar-promoted check: only meaningful when the tailored
            # claim has a non-empty identity. An empty-identity tailored item
            # (e.g. a pure category label that somehow made it past the
            # not-in-verified check) trivially subsets every Familiar identity
            # — guarding here avoids a false positive.
            if (
                not is_familiar_bucket
                and tailored_identity
                and any(
                    tailored_identity == f or tailored_identity.issubset(f)
                    for f in familiar_identity_sets
                    if f
                )
            ):
                msg = f"Familiar skill {item!r} promoted to category {cat.name!r}"
                raise FabricationError(
                    [FabricationViolation("familiar-promoted", item)], msg
                )
