"""Config loading. Single source of truth: ~/.config/jobhunt/config.toml.

Env vars override (prefix JOBHUNT_, double-underscore for nested keys).
Example: JOBHUNT_GATEWAY__BASE_URL overrides config.gateway.base_url.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

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


class IngestConfig(BaseModel):
    user_agent: str = "jobhunt/0.1 (+personal-use; user@example.com)"
    rate_limit_per_sec: float = 1.0
    cache_ttl_hours: int = 6
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)
    rss: list[str] = Field(default_factory=list)


class GatewayConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    tasks: dict[str, str] = Field(
        default_factory=lambda: {
            "score": "qwen3:8b",
            "tailor": "qwen3:14b",
            "cover": "qwen3:14b",
            "qa": "qwen3:8b",
            "embed": "nomic-embed-text",
        }
    )


class PipelineConfig(BaseModel):
    score_concurrency: int = 2
    tailor_max_words: int = 700
    cover_max_words: int = 280


class BrowserConfig(BaseModel):
    headed: bool = True
    user_data_dir: Path = Field(default_factory=lambda: _default_data_dir() / "browser-profile")


class Config(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)

    @classmethod
    def example_toml(cls) -> str:
        cfg = cls()
        return tomli_w.dumps(_to_toml_dict(cfg.model_dump(mode="json")))


def _to_toml_dict(obj: Any) -> dict[str, Any]:
    """Coerce values into TOML-serializable types (paths -> str)."""
    out: dict[str, Any] = {}
    for k, v in obj.items():
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
        path = key[len(ENV_PREFIX):].lower().split(ENV_NESTED_SEP)
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
