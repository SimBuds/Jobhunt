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


def _default_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "jobhunt" / "config.toml"


def _default_data_dir() -> Path:
    return Path.cwd() / "data"


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
    # Profile-specific filter: drop ML scientist / research engineer / data
    # platform / quant titles at ingest. Off by default — only enable for
    # profiles (e.g. frontend / CMS / full-stack) where these roles are never
    # a fit. See `ingest._filter.is_research_title`.
    drop_research_titles: bool = False


class GatewayConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    tasks: dict[str, str] = Field(
        default_factory=lambda: {
            # Base qwen3.5:9b. The gateway sends its own task prompt (overriding
            # any Modelfile SYSTEM) and its own options (gateway _DEFAULT_OPTIONS),
            # so behavior is fully defined in-repo — no custom Modelfile needed.
            # The critical app-owned option is num_ctx=16384: these prompts run
            # ~6k+ tokens and Ollama's 4096 default would truncate them into prose
            # (qwen-custom only worked by baking num_ctx). Single hot model across
            # all slots; no intra-scan reload churn. Quality backed by
            # deterministic post-processing (score clamp, cover validator + retry,
            # audit). (qwen-custom remains Casey's general chat model in ~/ai.)
            "score": "qwen3.5:9b",
            "tailor": "qwen3.5:9b",
            "cover": "qwen3.5:9b",
            "answer": "qwen3.5:9b",
            "embed": "nomic-embed-text",
        }
    )


class PipelineConfig(BaseModel):
    score_concurrency: int = 2
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
    # Default lowered from 65 to 55 in May 2026. Casey's interview-rate problem
    # is volume-of-good-applications, not noise-in-the-list — the 55-65 band is
    # the "stretch, tailor required" zone where a strong AI/LLM cover hook can
    # break through. Raise back to 65 via config.toml if the list gets noisy.
    min_score: int = 55


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
    work_arrangements: list[Literal["onsite", "hybrid", "remote"]] = Field(
        default_factory=lambda: ["onsite", "hybrid", "remote"]
    )
    employment_types: list[
        Literal["full_time", "part_time", "contract", "internship", "temporary"]
    ] = Field(default_factory=lambda: ["full_time", "contract"])


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
