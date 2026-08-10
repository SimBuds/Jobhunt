"""Config loading. Single source of truth: ~/.config/jobhunt/config.toml.

Env vars override (prefix JOBHUNT_, double-underscore for nested keys).
Example: JOBHUNT_GATEWAY__BASE_URL overrides config.gateway.base_url.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, Field

from jobhunt.errors import ConfigError

ENV_PREFIX = "JOBHUNT_"
ENV_NESTED_SEP = "__"

WorkArrangement = Literal["onsite", "hybrid", "remote"]
EmploymentType = Literal["full_time", "part_time", "contract", "internship", "temporary"]


def _default_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "jobhunt" / "config.toml"


def _default_data_dir() -> Path:
    return Path.cwd() / "data"


def _default_work_arrangements() -> list[WorkArrangement]:
    return ["onsite", "hybrid", "remote"]


def _default_employment_types() -> list[EmploymentType]:
    return ["full_time", "contract"]


class PathsConfig(BaseModel):
    data_dir: Path = Field(default_factory=_default_data_dir)
    db_path: Path = Field(default_factory=lambda: _default_data_dir() / "jobhunt.db")
    migrations_dir: Path = Field(default_factory=lambda: Path.cwd() / "migrations")
    kb_dir: Path = Field(default_factory=lambda: Path.cwd() / "kb")


class AdzunaConfig(BaseModel):
    # Empty list → auto-derive from `kb/profile/verified.json` via
    # `ingest._query_planner.derive_adzuna_queries`. Populate to override
    # with a verbatim list. See README §Configure ingest sources.
    queries: list[str] = Field(default_factory=list)
    pages: int = 3
    results_per_page: int = 50


class IngestConfig(BaseModel):
    user_agent: str = "jobhunt/0.1 (+personal-use; your-email@example.com)"
    rate_limit_per_sec: float = 1.0
    cache_ttl_hours: int = 6
    # Drop postings older than N days at ingest. 0 disables. Per-run override
    # via `jobhunt scan --max-age-days N`. Adapters that don't populate
    # `posted_at` (Workday) pass the filter — treated as fresh.
    max_age_days: int = 7
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)
    smartrecruiters: list[str] = Field(default_factory=list)
    # Each entry is "tenant:host:site" — e.g. "rbc:wd3:RBC_Careers". See
    # ingest/workday.py for how to find these values for a given employer.
    workday: list[str] = Field(default_factory=list)
    workable: list[str] = Field(default_factory=list)
    recruitee: list[str] = Field(default_factory=list)
    job_bank_ca: list[str] = Field(default_factory=list)
    rss: list[str] = Field(default_factory=list)
    adzuna: AdzunaConfig = Field(default_factory=AdzunaConfig)
    # After ingest, probe public ATS APIs for slugs of newly-seen companies
    # and append hits to config.toml so the next scan pulls deep JDs natively.
    # Toggle off if you want the legacy maintenance-only `discover slugs` flow.
    auto_discover: bool = True
    # Skip the post-ingest discovery probe while the actionable backlog (scored
    # at or above `[pipeline] min_score`, unapplied, not declined) is already
    # this deep. Widening intake past this point produces candidates that are
    # never consumed — the 2026-07-24 audit found a 113-job backlog draining at
    # roughly zero while discovery kept running. 0 disables the gate.
    # Composes with `auto_discover`: False there still means "never probe".
    discover_backlog_ceiling: int = 40
    # Profile-specific filter: drop ML scientist / research engineer / data
    # platform / quant titles at ingest. Off by default — only enable for
    # profiles (e.g. frontend / CMS / full-stack) where these roles are never
    # a fit. See `ingest._filter.is_research_title`.
    drop_research_titles: bool = False
    # Drop clearly non-engineering function titles at ingest (Office Administrator,
    # Sanitation, Food Safety, Maintenance Technician, Account Executive, Legal
    # Counsel, Buyer, etc.) so the scorer isn't burned on roles it always declines.
    # Default-on (unlike drop_research_titles): non-eng functions are never a fit
    # for any engineering profile. A dev/eng signal in the title always wins —
    # see `ingest._filter.is_non_engineering_title`. Set False to disable.
    drop_non_engineering_titles: bool = True


class GatewayConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    tasks: dict[str, str] = Field(
        default_factory=lambda: {
            # Base qwen3.5:9b. The gateway sends its own task prompt (overriding
            # any Modelfile SYSTEM) and its own options (gateway _DEFAULT_OPTIONS),
            # so behavior is fully defined in-repo — no custom Modelfile needed.
            # The critical app-owned option is num_ctx=16384: these prompts
            # measure 7.7k-9.4k tokens and Ollama's 4096 default would truncate
            # them into prose (qwen-custom only worked by baking num_ctx).
            # Single hot model across all slots; no intra-scan reload churn.
            # Quality backed by deterministic post-processing (tiered score
            # computation, cover validator + retry, audit). (qwen-custom
            # remains the candidate's general chat model in ~/ai.)
            "score": "qwen3.5:9b",
            "tailor": "qwen3.5:9b",
            "cover": "qwen3.5:9b",
            "answer": "qwen3.5:9b",
            "embed": "nomic-embed-text",
        }
    )


class PipelineConfig(BaseModel):
    tailor_max_words: int = 700
    cover_max_words: int = 280
    cover_retry_attempts: int = 3
    # Mirrors cover_retry_attempts. When _enforce_no_fabrication rejects a
    # tailored resume (LLM leaks an unverified skill like 'Redux'), the retry
    # layer re-prompts with a "REMOVE X" hint. Most fabrications recover on
    # attempt 2; 3 attempts is the same backstop the cover-letter loop uses.
    tailor_retry_attempts: int = 3
    # Default word cap for `jobhunt answer` responses. Short-factual
    # questions ("years of TypeScript?") need ~25 words; STAR-style
    # behavioural questions land closer to 150-200. 200 is a sane default
    # that the CLI's `--max-words` flag overrides per-call.
    answer_max_words: int = 200
    # Default lowered from 65 to 55 in May 2026. The candidate's interview-rate problem
    # is volume-of-good-applications, not noise-in-the-list — the 55-65 band is
    # the "stretch, tailor required" zone where a strong AI/LLM cover hook can
    # break through. Raise back to 65 via config.toml if the list gets noisy.
    min_score: int = 55
    # Confidence ceiling for signal-poor JDs (2026-05-31 audit fix). Without a
    # ceiling a ~500-char Adzuna snippet scored 82 while the same role's
    # 7,140-char full JD scored 55: the model cannot penalize requirements it
    # cannot see, so a snippet reads as near-full coverage. Capping thin
    # postings here keeps them applyable (> min_score) without letting them
    # outrank fully-described roles. Tune up if the thin-JD band looks
    # under-ranked, down to push snippet roles further below full-JD ones.
    # See pipeline.score.score_job.
    thin_jd_score_cap: int = 70
    # A JD shorter than this (chars) is treated as signal-poor for the cap above.
    # Adzuna snippets run ~500 chars; real ATS JDs 4,000-7,000. 800 matches the
    # "short JD signals Adzuna" threshold the audit must-have fallback already
    # uses, and exempts full-JD `apply --url` fetches.
    thin_jd_chars: int = 800

    # --- Score weights (2026-07-28) -------------------------------------
    # The model extracts the posting's requirements into two tiers; the score
    # is computed from how many verify against verified.json:
    #
    #   score = base + tier1_weight * tier1_coverage
    #                + tier2_weight * tier2_coverage
    #                + ai_bonus
    #
    # Coverage is graded: an exact profile hit contributes 1.0, a peer-family
    # or annotated-bridge hit contributes `score_transferable_credit`. An empty
    # tier-2 folds its weight into tier-1, so a posting cannot score higher for
    # simply omitting a wish list.
    #
    # These are the calibration dial. Defaults put a perfect fit with the AI
    # bonus at 95, a full core-stack match with wish-list gaps at 80, half the
    # hard requirements at 60, and nothing matched at 30. All five feed
    # `pipeline.score.prompt_hash`, so changing any of them re-scores the
    # backlog on the next `scan` — that is deliberate, since a weight change
    # makes existing scores incomparable.
    score_base: int = 30
    score_tier1_weight: int = 50
    score_tier2_weight: int = 10
    score_ai_bonus: int = 5
    score_transferable_credit: float = 0.7

    # --- Band separation (2026-08-10) -----------------------------------
    # Deterministic ceiling for Senior/Sr./Lead/Staff/Principal/Architect
    # titles, applied on the TITLE ALONE.
    #
    # This replaces a ceiling that could never fire. The old one was gated on
    # the model emitting a "Senior-band" decline_reason, but the July 2026
    # prompt rewrite told the model to stop declining senior titles precisely
    # because "a deterministic ceiling downstream already keeps them from
    # outranking full fits". It did not: measured on the 650-score backlog,
    # the senior_band cap fired on 0 of 62 undeclined senior-titled roles, and
    # senior titles ended up with a HIGHER median (60) than explicit
    # junior/mid ones (50) — the opposite of the intent.
    #
    # Set at 60 so senior roles stay visible just above `min_score` as
    # deliberate stretch applications, while any junior/mid or unbanded role
    # with real coverage outranks them. Raise toward 70 to weight senior
    # postings back up, or below `min_score` to drop them out of the list
    # entirely without touching `include_senior_roles`.
    senior_score_cap: int = 60
    # Additive preference for titles that explicitly say Junior, Jr,
    # Intermediate, Mid, Associate, Developer I/II, new grad, co-op, or intern.
    # These are the roles worth chasing at 3 YoE, and they were ranking below
    # senior postings because nothing in the model rewarded the band. Applied
    # before every ceiling, so it lifts ranking within the band without
    # letting a thin JD or a Familiar-only fit escape its cap.
    junior_score_bonus: int = 5


class BrowserConfig(BaseModel):
    headed: bool = True
    user_data_dir: Path = Field(default_factory=lambda: _default_data_dir() / "browser-profile")


class ApplicantProfile(BaseModel):
    """Answers to common application form questions that aren't on the resume."""

    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    city: str = "Toronto"
    region: str = "Ontario"
    country: str = "Canada"
    work_auth_canada: bool = True
    requires_visa_sponsorship: bool = False
    salary_expectation_cad: str = ""
    # Years of professional dev experience. Threaded into the score prompt so
    # auto-decline rules track the candidate's actual band (years required >
    # YoE + 3 declines). None disables YoE-aware scoring; run `jobhunt setup`
    # to populate.
    years_experience: int | None = None
    # Include Senior / Sr. / Lead / Staff / Principal / Architect titles in
    # scan results. Defaults True (legacy behavior); set False to drop them
    # at ingest. Independent of years_experience — some candidates with
    # <4 YoE still want to see Senior postings (loosely-titled startups),
    # and some senior candidates explicitly don't want Staff+ roles.
    include_senior_roles: bool = True
    pronouns: str = ""
    work_arrangements: list[WorkArrangement] = Field(
        default_factory=_default_work_arrangements
    )
    employment_types: list[EmploymentType] = Field(
        default_factory=_default_employment_types
    )


class Config(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    applicant: ApplicantProfile = Field(default_factory=ApplicantProfile)

    @classmethod
    def example_toml(cls) -> str:
        cfg = cls()
        return tomli_w.dumps(_to_toml_dict(cfg.model_dump(mode="json")))


def _to_toml_dict(obj: Any) -> dict[str, Any]:
    """Coerce values into TOML-serializable types.

    Paths become strings, and None-valued keys are dropped — TOML has no
    null literal, and Pydantic will re-populate optional fields from their
    declared defaults on the next load."""
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if v is None:
            continue
        if isinstance(v, dict):
            out[k] = _to_toml_dict(v)
        elif isinstance(v, Path):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX) :].lower().split(ENV_NESTED_SEP)
        cursor: dict[str, Any] = data
        for part in path[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[path[-1]] = value
    return data


def load_config(path: Path | None = None, *, write_default_if_missing: bool = True) -> Config:
    cfg_path = path or _default_config_path()
    if not cfg_path.exists():
        if write_default_if_missing:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(Config.example_toml())
        data: dict[str, Any] = {}
    else:
        try:
            data = tomllib.loads(cfg_path.read_text())
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"failed to parse {cfg_path}: {e}") from e

    data = _apply_env_overrides(data)
    try:
        return Config.model_validate(data)
    except Exception as e:
        raise ConfigError(f"invalid config: {e}") from e


def config_path() -> Path:
    return _default_config_path()
