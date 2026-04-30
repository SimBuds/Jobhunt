"""Load API keys from ~/.config/jobhunt/secrets.toml or JOBHUNT_* env vars."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

from jobhunt.config import _default_config_path
from jobhunt.errors import ConfigError


class Secrets(BaseModel):
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None


def secrets_path() -> Path:
    return _default_config_path().with_name("secrets.toml")


def load_secrets() -> Secrets:
    data: dict[str, str] = {}
    p = secrets_path()
    if p.exists():
        try:
            data = tomllib.loads(p.read_text())
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"failed to parse {p}: {e}") from e
    for key in ("adzuna_app_id", "adzuna_app_key"):
        env = os.environ.get(f"JOBHUNT_{key.upper()}")
        if env:
            data[key] = env
    return Secrets.model_validate(data)
