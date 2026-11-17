from __future__ import annotations

import os
from pathlib import Path

import pytest

LIVE_RESUME_SKIP = "live resume not supplied: pass --live-resume PATH"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-resume",
        metavar="PATH",
        default=None,
        help=(
            "Run the live-resume parser tests against this .docx, read only. "
            "Off by default: the suite never reads personal data unless asked."
        ),
    )


@pytest.fixture
def live_resume(request: pytest.FixtureRequest) -> Path:
    """The real resume, only when the run asks for it (Phase 19c).

    The resume left the repo root for the private profile dir, so the old
    discovery would skip these tests silently. An explicit option keeps the
    default run off personal data, and the summary line keeps the skip visible.
    """
    raw = request.config.getoption("--live-resume")
    if raw is None:
        pytest.skip(LIVE_RESUME_SKIP)
    path = Path(raw).expanduser()
    if not path.is_file():
        pytest.fail(f"--live-resume {path} is not a file")
    return path


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    skipped = [
        r for r in terminalreporter.stats.get("skipped", []) if LIVE_RESUME_SKIP in str(r.longrepr)
    ]
    if skipped:
        terminalreporter.write_line(
            f"{len(skipped)} live-resume test(s) not run: pass --live-resume PATH to run them."
        )


@pytest.fixture(autouse=True)
def _isolate_user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs against a tmp app folder and no other JOBHUNT_* overrides.

    Config, secrets and data defaults resolve under `JOBHUNT_HOME`, and the
    real config.toml names the real database. Without this, a test that loads
    config without `tmp_config_dir` reads or writes Casey's data (2026-11-02).
    """
    for key in [k for k in os.environ if k.startswith("JOBHUNT_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("JOBHUNT_HOME", str(tmp_path / "app"))


@pytest.fixture(autouse=True)
def _restore_umask():
    """The CLI callback sets umask 077 process-wide (Phase 18). Restore it after
    every test, so a test that runs the CLI cannot change the modes the next
    test's files are created with."""
    previous = os.umask(0o022)
    os.umask(previous)
    yield
    os.umask(previous)


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp app folder at `<returned>/jobhunt`, so tests write config to
    `tmp_config_dir / "jobhunt" / "config.toml"` and config writes stay in tmp."""
    config_home = tmp_path / "config"
    config_home.mkdir()
    monkeypatch.setenv("JOBHUNT_HOME", str(config_home / "jobhunt"))
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
