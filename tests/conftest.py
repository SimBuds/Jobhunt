from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs against tmp XDG directories and no JOBHUNT_* overrides.

    Private-path defaults resolve under XDG_DATA_HOME, and the real
    config.toml (under XDG_CONFIG_HOME) names the real database. Without this,
    a test that loads config without `tmp_config_dir` reads or writes Casey's
    data (2026-11-02).
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    for key in [k for k in os.environ if k.startswith("JOBHUNT_")]:
        monkeypatch.delenv(key)


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect XDG_CONFIG_HOME to a tmp dir so config writes don't escape."""
    config_home = tmp_path / "config"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    return config_home


@pytest.fixture
def migrations_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "migrations"


FIXTURE_PROFILE = Path(__file__).resolve().parent / "fixtures" / "profile" / "verified.json"


@pytest.fixture
def verified() -> dict:
    """The fictional profile every test asserts against.

    Tests used to read the repo's own `kb/profile/verified.json`, falling back
    to an inline stub only when the file was absent. That coupled the suite to
    a personal, **gitignored**, hand-edited document: on 2026-07-24 a resume
    rewrite turned 11 tests red, and the same suite would pass or fail on
    another machine depending on whose resume was checked out. The live file is
    still exercised — by exactly one smoke test in `test_parse_docx.py` that
    asserts it parses cleanly, which is the real regression signal.
    """
    import json

    return json.loads(FIXTURE_PROFILE.read_text(encoding="utf-8"))
