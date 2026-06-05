"""Tests for `jobhunt setup` — the first-run guided wizard."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobhunt.cli import app
from jobhunt.commands import config_cmd, convert_resume_cmd, setup_cmd


def _seed_minimal_config(config_dir: Path) -> Path:
    """Write a minimal config.toml the wizard can read + write back."""
    jh_dir = config_dir / "jobhunt"
    jh_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = jh_dir / "config.toml"
    cfg_path.write_text(
        "[paths]\n"
        f'kb_dir = "{config_dir / "kb"}"\n'
        f'db_path = "{config_dir / "data" / "jobhunt.db"}"\n'
        f'migrations_dir = "{config_dir / "migrations"}"\n'
        "[ingest]\n"
        "greenhouse = []\n"
        "[applicant]\n"
        "full_name = \"Test User\"\n"
        "email = \"test@example.com\"\n"
    )
    # Pre-create the verified profile so the wizard's "re-parse?" branch
    # treats it as up-to-date.
    profile = config_dir / "kb" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "verified.json").write_text("{}")
    (config_dir / "migrations").mkdir(parents=True, exist_ok=True)
    (config_dir / "data").mkdir(parents=True, exist_ok=True)
    return cfg_path


def test_setup_wizard_writes_applicant_fields(
    tmp_config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_path = _seed_minimal_config(tmp_config_dir)

    # Work in a tmp cwd so the wizard finds Baseline_Resume.docx in the expected spot.
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "Baseline_Resume.docx").write_bytes(b"")  # presence is enough
    monkeypatch.chdir(workdir)

    # Make verified.json newer than Baseline_Resume.docx so the re-parse branch is
    # skipped (and convert_resume_cmd.run is never called even if mocked).
    verified = tmp_config_dir / "kb" / "profile" / "verified.json"
    import os
    now = (workdir / "Baseline_Resume.docx").stat().st_mtime
    os.utime(verified, (now + 10, now + 10))

    # Mock the heavyweight steps: DB migrate, convert-resume, config seed.
    monkeypatch.setattr(setup_cmd, "migrate", lambda *_a, **_k: None)
    monkeypatch.setattr(convert_resume_cmd, "run", lambda **_k: None)
    seed_calls: list[tuple[bool, bool]] = []
    monkeypatch.setattr(
        config_cmd,
        "seed",
        lambda preview=False, apply=False: seed_calls.append((preview, apply)),
    )

    runner = CliRunner()
    # Prompt sequence (in order, see setup_cmd._step_*):
    #   1. "re-parse anyway?" (verified.json newer) -> "n"
    #   2. years_experience -> "3"
    #   3. include_senior_roles confirm -> "n"
    #   4. salary_expectation_cad -> "60k-90k"
    #   5. work_arrangements -> "remote,hybrid"
    #   6. employment_types -> "full_time,contract"
    #   7. apply seed list? -> "n"
    inputs = "\n".join(["n", "3", "n", "60k-90k", "remote,hybrid", "full_time,contract", "n", ""])
    result = runner.invoke(app, ["setup"], input=inputs)
    assert result.exit_code == 0, result.output

    with cfg_path.open("rb") as f:
        written = tomllib.load(f)
    a = written["applicant"]
    assert a["years_experience"] == 3
    assert a["include_senior_roles"] is False
    assert a["salary_expectation_cad"] == "60k-90k"
    assert a["work_arrangements"] == ["remote", "hybrid"]
    assert a["employment_types"] == ["full_time", "contract"]

    # Seed preview was offered; user declined apply, so only one seed() call
    # (preview=True), no apply=True call.
    assert (True, False) in seed_calls
    assert (False, True) not in seed_calls


def test_setup_wizard_exits_cleanly_when_resume_missing(
    tmp_config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_minimal_config(tmp_config_dir)
    workdir = tmp_path / "no-resume"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    monkeypatch.setattr(setup_cmd, "migrate", lambda *_a, **_k: None)

    runner = CliRunner()
    # The "place resume now?" prompt → "n". Wizard prints pause msg and exits 0.
    result = runner.invoke(app, ["setup"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "setup paused" in result.output
