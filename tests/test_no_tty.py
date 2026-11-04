"""Nothing waits for input without a terminal (Phase 22d, 2026-11-04).

An automated caller runs jobhunt with stdin at /dev/null or as an open pipe
it never writes to. A prompt on /dev/null aborts with exit 1, and a prompt on
an open pipe waits forever. Each prompting command instead checks for a
terminal at entry and exits 11 (needs a person) with one line saying what to
do. `--force` paths and `apply --top N --no-browser` still run unattended.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobhunt import cli, commands
from jobhunt.errors import NeedsPersonError

REPO = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.executable).parent / "jobhunt"
TIMEOUT = 10


def _env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("JOBHUNT_")}
    env |= {"XDG_CONFIG_HOME": str(tmp_path / "cfg"), "XDG_DATA_HOME": str(tmp_path / "data")}
    return env


def _profile(tmp_path: Path) -> None:
    profile = tmp_path / "data" / "jobhunt" / "profile"
    profile.mkdir(parents=True)
    (profile / "verified.json").write_text("{}")


def _jobhunt(
    tmp_path: Path, args: list[str], *, open_pipe: bool
) -> subprocess.CompletedProcess[str]:
    """Run the installed script. With `open_pipe`, stdin is a pipe that stays
    open and empty, which is what makes a prompt hang rather than abort."""
    proc = subprocess.Popen(
        [str(SCRIPT), *args], cwd=REPO, env=_env(tmp_path), text=True,
        stdin=subprocess.PIPE if open_pipe else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail(f"`jobhunt {' '.join(args)}` waited for input past {TIMEOUT}s")
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
    assert proc.stdout is not None and proc.stderr is not None
    out, err = proc.stdout.read(), proc.stderr.read()
    proc.stdout.close()
    proc.stderr.close()
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


PROMPTING = [
    ["setup"],
    ["db", "reset"],
    ["db", "gc", "--prune"],
    ["config", "reprobe", "--prune"],
    ["apply", "--best"],
]


@pytest.mark.parametrize("open_pipe", [False, True], ids=["devnull", "open-pipe"])
@pytest.mark.parametrize("args", PROMPTING, ids=[" ".join(a) for a in PROMPTING])
def test_prompting_command_exits_11_without_a_terminal(
    args: list[str], open_pipe: bool, tmp_path: Path
) -> None:
    _profile(tmp_path)
    proc = _jobhunt(tmp_path, args, open_pipe=open_pipe)
    assert proc.returncode == 11, (proc.stdout, proc.stderr)
    assert proc.stderr.startswith("error: ")
    assert proc.stderr.count("\n") == 1, proc.stderr
    assert "terminal" in proc.stderr


FORCED = [
    ["db", "reset", "--force"],
    ["db", "gc", "--prune", "--force"],
    ["config", "reprobe", "--prune", "--force"],
]


@pytest.mark.parametrize("args", FORCED, ids=[" ".join(a) for a in FORCED])
def test_force_runs_without_a_terminal(args: list[str], tmp_path: Path) -> None:
    proc = _jobhunt(tmp_path, args, open_pipe=True)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)


def test_apply_top_no_browser_never_prompts(tmp_path: Path) -> None:
    _profile(tmp_path)
    init = _jobhunt(tmp_path, ["db", "init"], open_pipe=True)
    assert init.returncode == 0, init.stderr
    proc = _jobhunt(tmp_path, ["apply", "--top", "1", "--no-browser"], open_pipe=True)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)


# --- in-process: the guard itself, and prompts unchanged with a terminal ------


def test_require_tty_raises_needs_person_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(NeedsPersonError, match="db reset asks to confirm"):
        commands.require_tty("db reset asks to confirm. Pass --force")


def test_require_tty_passes_with_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    commands.require_tty("anything")


def test_require_tty_treats_a_missing_stdin_as_no_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", None)
    with pytest.raises(NeedsPersonError):
        commands.require_tty("anything")


def test_with_a_terminal_db_reset_still_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jobhunt.config import Config

    Config().paths.db_path.parent.mkdir(parents=True)
    Config().paths.db_path.write_text("")
    monkeypatch.setattr(commands, "require_tty", lambda reason: None)
    result = CliRunner().invoke(cli.app, ["db", "reset"], input="no\n")
    assert "type 'yes' to confirm" in result.output
    assert "reset: cancelled." in result.output
    assert Config().paths.db_path.exists()
