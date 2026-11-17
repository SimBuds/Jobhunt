"""Private data lives in the app folder, and tests can never reach the real copy.

Phase PV1 (2026-11-17). Config, secrets, data, database, profile and
browser-profile defaults resolve under `app_root()`. `conftest._isolate_user_dirs`
points `JOBHUNT_HOME` at tmp and drops every other `JOBHUNT_*` variable,
because the real `config.toml` names the real database and a test that loads
config without isolation would use it.
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


def test_only_the_tmp_jobhunt_home_reaches_a_test(tmp_path: Path) -> None:
    leaked = [k for k in os.environ if k.startswith("JOBHUNT_") and k != "JOBHUNT_HOME"]
    assert leaked == []
    assert Path(os.environ["JOBHUNT_HOME"]).is_relative_to(tmp_path)


# --- Phase PV1 (2026-11-17): every default lives in the app folder ---------


def test_defaults_live_under_jobhunt_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jobhunt.config import config_path
    from jobhunt.secrets import secrets_path

    home = tmp_path / "app"
    monkeypatch.setenv("JOBHUNT_HOME", str(home))
    cfg = Config()
    assert config_path() == home / "config.toml"
    assert secrets_path() == home / "secrets.toml"
    assert (
        cfg.paths.data_dir,
        cfg.paths.db_path,
        cfg.paths.profile_dir,
        cfg.browser.user_data_dir,
    ) == (
        home / "data",
        home / "data" / "jobhunt.db",
        home / "data" / "profile",
        home / "data" / "browser-profile",
    )


def test_app_root_without_env_is_this_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    from jobhunt.config import app_root

    monkeypatch.delenv("JOBHUNT_HOME", raising=False)
    assert app_root() == REPO


def test_app_root_falls_back_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jobhunt import config

    monkeypatch.delenv("JOBHUNT_HOME", raising=False)
    monkeypatch.setattr(config, "_find_app_root", lambda start: None)
    monkeypatch.chdir(tmp_path)
    assert config.app_root() == tmp_path


def test_xdg_dirs_are_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jobhunt.config import config_path

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    for path in [config_path(), *_private_defaults(Config()).values()]:
        assert not path.is_relative_to(tmp_path / "xdg-config"), path
        assert not path.is_relative_to(tmp_path / "xdg-data"), path


def test_config_toml_beats_defaults_and_env_beats_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobhunt.config import config_path

    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
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


# --- Phase PV2 (2026-11-17): a managed .gitignore keeps personal files out --
#
# Personal files live in the app folder (PV1). The app keeps `.gitignore`
# covering them and refuses only when git already tracks one. These tests run
# real git in tmp folders, staging with `git add` only, never committing.


def _git(root: Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q")
    return root


def test_fresh_folder_gets_every_entry(tmp_path: Path) -> None:
    from jobhunt.privacy import IGNORED, ensure_gitignore

    added = ensure_gitignore(tmp_path)
    lines = (tmp_path / ".gitignore").read_text().splitlines()
    assert added == list(IGNORED)
    for entry in ("config.toml", "secrets.toml", "data/", "*.docx", "*.pdf"):
        assert entry in lines


def test_existing_file_gains_only_missing_lines(tmp_path: Path) -> None:
    from jobhunt.privacy import IGNORED, ensure_gitignore

    original = "# mine\n.venv/\n*.docx\ndata/\n"
    (tmp_path / ".gitignore").write_text(original)
    added = ensure_gitignore(tmp_path)
    text = (tmp_path / ".gitignore").read_text()
    assert text.startswith(original)
    assert added == [e for e in IGNORED if e not in ("*.docx", "data/")]
    assert text.count("*.docx") == 1


def test_second_run_changes_nothing(tmp_path: Path) -> None:
    from jobhunt.privacy import ensure_gitignore

    ensure_gitignore(tmp_path)
    before = (tmp_path / ".gitignore").read_text()
    assert ensure_gitignore(tmp_path) == []
    assert (tmp_path / ".gitignore").read_text() == before


def test_file_without_trailing_newline_is_not_glued(tmp_path: Path) -> None:
    from jobhunt.privacy import ensure_gitignore

    (tmp_path / ".gitignore").write_text(".venv/")
    ensure_gitignore(tmp_path)
    assert ".venv/" in (tmp_path / ".gitignore").read_text().splitlines()


@pytest.mark.parametrize("name", ["config.toml", "secrets.toml", "notes/Jane_Resume.docx"])
def test_tracked_private_file_refuses_with_the_fix(tmp_path: Path, name: str) -> None:
    from jobhunt.errors import PrivacyError
    from jobhunt.privacy import check_tracked

    root = _git_repo(tmp_path)
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("x")
    _git(root, "add", "-f", name)
    with pytest.raises(PrivacyError) as exc:
        check_tracked(root)
    assert name in str(exc.value)
    assert "git rm --cached" in str(exc.value)


def test_ignored_untracked_files_pass(tmp_path: Path) -> None:
    from jobhunt.privacy import check_tracked, ensure_gitignore

    root = _git_repo(tmp_path)
    ensure_gitignore(root)
    (root / "config.toml").write_text("x")
    (root / "data").mkdir()
    (root / "data" / "jobhunt.db").write_text("x")
    _git(root, "add", "-A")
    check_tracked(root)


def test_folder_without_git_passes(tmp_path: Path) -> None:
    from jobhunt.privacy import check_tracked

    (tmp_path / "config.toml").write_text("x")
    check_tracked(tmp_path)


def test_cli_writes_gitignore_in_the_app_folder() -> None:
    from typer.testing import CliRunner

    from jobhunt.cli import app
    from jobhunt.config import app_root

    CliRunner().invoke(app, ["list"])
    assert "config.toml" in (app_root() / ".gitignore").read_text().splitlines()


def test_cli_refuses_tracked_config_but_config_show_path_and_help_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from jobhunt.cli import app

    root = _git_repo(tmp_path)
    monkeypatch.setenv("JOBHUNT_HOME", str(root))
    (root / "config.toml").write_text("")
    _git(root, "add", "-f", "config.toml")
    runner = CliRunner()
    refused = runner.invoke(app, ["list"])
    assert refused.exit_code == 10
    assert "config.toml" in refused.output
    for argv in (["config", "show"], ["config", "path"], ["list", "--help"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, (argv, result.output)


# --- Phase 20 (2026-11-03): work-authorization defaults ship empty ----------
#
# The shipped defaults were `work_auth_canada = True` and
# `requires_visa_sponsorship = False`: one person's status, published as every
# user's default. Unset now means "not answered": form-fill leaves the field
# blank and the interview-prep prompt says "not set".


def _field_map(profile):
    from jobhunt.browser.profile_map import build_field_map

    return build_field_map(profile, resume_path=Path("r.docx"), cover_path=Path("c.docx"))


def test_work_auth_defaults_are_unset() -> None:
    from jobhunt.config import ApplicantProfile

    profile = ApplicantProfile()
    assert (profile.work_auth_canada, profile.requires_visa_sponsorship) == (None, None)


def test_unset_work_auth_leaves_form_fields_blank() -> None:
    from jobhunt.config import ApplicantProfile

    fields = _field_map(ApplicantProfile())
    assert (fields["work_auth_canada"], fields["requires_visa_sponsorship"]) == ("", "")


@pytest.mark.parametrize(
    ("auth", "sponsor", "expected"),
    [(True, False, ("Yes", "No")), (False, True, ("No", "Yes"))],
)
def test_set_work_auth_maps_to_yes_no(auth: bool, sponsor: bool, expected: tuple[str, str]) -> None:
    from jobhunt.config import ApplicantProfile

    fields = _field_map(ApplicantProfile(work_auth_canada=auth, requires_visa_sponsorship=sponsor))
    assert (fields["work_auth_canada"], fields["requires_visa_sponsorship"]) == expected


@pytest.mark.parametrize(("value", "text"), [(True, "true"), (False, "false"), (None, "not set")])
def test_interview_prep_renders_the_flag(value: bool | None, text: str) -> None:
    from jobhunt.pipeline.interview_prep import _flag_text

    assert _flag_text(value) == text
