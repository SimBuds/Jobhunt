"""Private data lives outside the repo, and tests can never reach the real copy.

Phase 17a (2026-11-02). The data, database, profile and browser-profile
defaults resolve under the XDG data directory instead of the working
directory. `conftest._isolate_user_dirs` points both XDG directories at tmp
and drops every `JOBHUNT_*` variable, because the real `config.toml` names the
real database and a test that loads config without isolation would use it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jobhunt.config import Config, load_config

REPO = Path(__file__).resolve().parent.parent


def _private_defaults(cfg: Config) -> dict[str, Path]:
    return {
        "data_dir": cfg.paths.data_dir,
        "db_path": cfg.paths.db_path,
        "profile_dir": cfg.paths.profile_dir,
        "user_data_dir": cfg.browser.user_data_dir,
    }


def test_defaults_resolve_inside_the_test_tmp_dir(tmp_path: Path) -> None:
    for name, path in _private_defaults(Config()).items():
        assert path.is_relative_to(tmp_path), (name, path)
        assert not path.is_relative_to(REPO), (name, path)


def test_no_jobhunt_env_var_reaches_a_test() -> None:
    assert [k for k in os.environ if k.startswith("JOBHUNT_")] == []


def test_falls_back_to_home_local_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    paths = Config().paths
    base = tmp_path / "home" / ".local" / "share" / "jobhunt"
    assert (paths.data_dir, paths.db_path, paths.profile_dir) == (
        base,
        base / "jobhunt.db",
        base / "profile",
    )


def test_config_toml_beats_defaults_and_env_beats_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    (config_home / "jobhunt").mkdir(parents=True, exist_ok=True)
    (config_home / "jobhunt" / "config.toml").write_text(
        "[paths]\n"
        f'data_dir = "{tmp_path / "toml-data"}"\n'
        f'profile_dir = "{tmp_path / "toml-profile"}"\n'
    )
    cfg = load_config()
    assert cfg.paths.data_dir == tmp_path / "toml-data"
    assert cfg.paths.profile_dir == tmp_path / "toml-profile"
    # Independent defaults: setting data_dir does not move db_path.
    assert cfg.paths.db_path.parent != tmp_path / "toml-data"

    monkeypatch.setenv("JOBHUNT_PATHS__PROFILE_DIR", str(tmp_path / "env-profile"))
    assert load_config().paths.profile_dir == tmp_path / "env-profile"
