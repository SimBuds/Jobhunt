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


# --- Phase 19a (2026-11-03): refuse to run with private data in the repo ----
#
# conftest's autouse `_fake_repo_root` points `privacy.repo_root()` at an empty
# tmp "checkout", so these tests build their own leftovers there and no CLI
# test ever sees the real repo.


def _repo(tmp_path: Path) -> Path:
    from jobhunt import privacy

    root = privacy.repo_root()
    assert root is not None and root.is_relative_to(tmp_path)
    return root


@pytest.mark.parametrize("field", ["data_dir", "db_path", "profile_dir"])
def test_private_path_inside_the_repo_is_refused(tmp_path: Path, field: str) -> None:
    from jobhunt.errors import PrivacyError
    from jobhunt.privacy import check_private_paths

    root = _repo(tmp_path)
    cfg = Config()
    setattr(cfg.paths, field, root / "data" / field)
    with pytest.raises(PrivacyError) as exc:
        check_private_paths(cfg, root)
    assert field in str(exc.value)
    assert str(root) in str(exc.value)


def test_browser_profile_inside_the_repo_is_refused(tmp_path: Path) -> None:
    from jobhunt.errors import PrivacyError
    from jobhunt.privacy import check_private_paths

    root = _repo(tmp_path)
    cfg = Config()
    cfg.browser.user_data_dir = root / "browser-profile"
    with pytest.raises(PrivacyError, match="user_data_dir"):
        check_private_paths(cfg, root)


def test_symlink_into_the_repo_is_refused(tmp_path: Path) -> None:
    from jobhunt.errors import PrivacyError
    from jobhunt.privacy import check_private_paths

    root = _repo(tmp_path)
    (root / "hidden").mkdir()
    link = tmp_path / "looks-outside"
    link.symlink_to(root / "hidden")
    cfg = Config()
    cfg.paths.profile_dir = link
    with pytest.raises(PrivacyError, match="profile_dir"):
        check_private_paths(cfg, root)


def test_paths_outside_the_repo_pass(tmp_path: Path) -> None:
    from jobhunt.privacy import check_private_paths

    check_private_paths(Config(), _repo(tmp_path))  # defaults live under tmp XDG


@pytest.mark.parametrize("leftover", ["kb/profile", "data"])
def test_leftover_private_dir_in_the_repo_is_refused(tmp_path: Path, leftover: str) -> None:
    from jobhunt.errors import PrivacyError
    from jobhunt.privacy import check_private_paths

    root = _repo(tmp_path)
    (root / leftover).mkdir(parents=True)
    with pytest.raises(PrivacyError) as exc:
        check_private_paths(Config(), root)
    assert str(root / leftover) in str(exc.value)


def test_resume_in_the_repo_root_is_refused_and_named(tmp_path: Path) -> None:
    from jobhunt.errors import PrivacyError
    from jobhunt.privacy import check_private_paths

    root = _repo(tmp_path)
    (root / "Jane_Dev_Resume.docx").write_bytes(b"")
    with pytest.raises(PrivacyError) as exc:
        check_private_paths(Config(), root)
    message = str(exc.value)
    assert "Jane_Dev_Resume.docx" in message
    assert "--docx" in message
    assert str(Config().paths.profile_dir) in message


def test_no_checkout_passes() -> None:
    """An installed copy has no repo root, so there is nothing to protect."""
    from jobhunt.privacy import check_private_paths

    check_private_paths(Config(), None)


def test_real_finder_returns_this_checkout() -> None:
    """The autouse fixture replaces `repo_root()`, so the production finder is
    tested here directly. Without this, the fixture would hide a broken one."""
    from jobhunt import privacy

    assert privacy._find_repo_root(Path(privacy.__file__)) == REPO


def test_real_finder_outside_a_checkout_returns_none(tmp_path: Path) -> None:
    from jobhunt.privacy import _find_repo_root

    fake = tmp_path / "site-packages" / "jobhunt" / "privacy.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("")
    assert _find_repo_root(fake) is None


def test_cli_refuses_but_config_show_path_and_help_still_run(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from jobhunt.cli import app

    root = _repo(tmp_path)
    (root / "kb" / "profile").mkdir(parents=True)
    runner = CliRunner()
    refused = runner.invoke(app, ["list"])
    assert refused.exit_code != 0
    assert str(root / "kb" / "profile") in refused.output
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

    fields = _field_map(
        ApplicantProfile(work_auth_canada=auth, requires_visa_sponsorship=sponsor)
    )
    assert (fields["work_auth_canada"], fields["requires_visa_sponsorship"]) == expected


@pytest.mark.parametrize(("value", "text"), [(True, "true"), (False, "false"), (None, "not set")])
def test_interview_prep_renders_the_flag(value: bool | None, text: str) -> None:
    from jobhunt.pipeline.interview_prep import _flag_text

    assert _flag_text(value) == text
