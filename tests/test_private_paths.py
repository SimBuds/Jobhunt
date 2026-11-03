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


# --- Phase 18 (2026-11-03): private files are created user-only -------------
#
# The CLI callback sets umask 077, so every file the app or its children
# (SQLite, Chromium) create is 0600 and every directory 0700. umask is
# process-wide, so conftest's autouse `_restore_umask` puts it back after every
# test.


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_db_init_creates_user_only_files(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from jobhunt.cli import app

    os.umask(0o022)  # the common shell default, so the test cannot pass by luck
    result = CliRunner().invoke(app, ["db", "init"])
    assert result.exit_code == 0, result.output
    paths = Config().paths
    assert _mode(paths.data_dir) == 0o700
    assert _mode(paths.db_path) == 0o600


def test_existing_file_mode_is_left_alone(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from jobhunt.cli import app

    os.umask(0o022)
    existing = Config().paths.data_dir / "notes.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("kept")
    existing.chmod(0o644)
    result = CliRunner().invoke(app, ["db", "init"])
    assert result.exit_code == 0, result.output
    # Tightening existing files is the migration's job, done once and visibly,
    # never a side effect of running a command.
    assert _mode(existing) == 0o644
