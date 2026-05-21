"""Phase 10 tests — analyze validators + categorize_violation."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobhunt.cli import app
from jobhunt.pipeline.cover_validate import categorize_violation


def _seed_minimal_config(config_dir: Path, data_dir: Path) -> Path:
    jh_dir = config_dir / "jobhunt"
    jh_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = jh_dir / "config.toml"
    cfg_path.write_text(
        "[paths]\n"
        f'data_dir = "{data_dir}"\n'
        f'db_path = "{data_dir / "jobhunt.db"}"\n'
        f'kb_dir = "{config_dir / "kb"}"\n'
    )
    profile = config_dir / "kb" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "verified.json").write_text("{}")
    return cfg_path


def _write_audit(data_dir: Path, job_safe_id: str, violations: list[str]) -> Path:
    d = data_dir / "applications" / job_safe_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "audit.json"
    p.write_text(json.dumps({
        "verdict": "revise",
        "keyword_coverage_pct": 80,
        "matched_keywords": [],
        "missing_must_haves": [],
        "fabrication_flags": [],
        "cover_letter_violations": violations,
        "alignment_flags": [],
    }))
    return p


# --- categorize_violation --------------------------------------------------


@pytest.mark.parametrize("msg,expected", [
    ("banned phrase: 'spearheaded'", "banned_phrase"),
    ("form-letter opener: 'i am writing to'", "banned_opener"),
    ("unverified number: '42'", "unverified_number"),
    ("unverified tech claim: 'kubernetes'", "unverified_tech"),
    ("body is 320 words; max is 280", "word_count_over"),
    ("expected 3-4 paragraphs; got 5", "paragraph_count"),
    ("lead paragraph does not name company 'Acme'", "company_missing"),
    ("salutation: 'To whom it may concern' is banned", "banned_salutation"),
    ("body contains an exclamation mark", "exclamation"),
    ("body contains an unfilled template placeholder", "template_placeholder"),
    ("body recaps resume material: 'dean's list'", "recap_in_body"),
    ("paragraph 2 ends with a sign-off line", "sign_off_in_body"),
])
def test_categorize_known_prefixes(msg: str, expected: str) -> None:
    assert categorize_violation(msg) == expected


def test_categorize_unknown_falls_through_to_underscore_id() -> None:
    """Defensive-pattern labels and any unmatched message pass through with
    a sanitized rule_id so they still aggregate cleanly."""
    out = categorize_violation("defensive: 'rather than X'")
    # No matched prefix — falls to lower-case + underscored.
    assert " " not in out
    assert out.startswith("defensive:")


def test_categorize_strips_to_80_chars() -> None:
    long_msg = "x" * 200
    assert len(categorize_violation(long_msg)) == 80


# --- analyze validators ----------------------------------------------------


def test_analyze_validators_aggregates_by_rule(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    _seed_minimal_config(tmp_config_dir, data_dir)
    _write_audit(data_dir, "job1", [
        "banned phrase: 'spearheaded'",
        "unverified number: '42'",
    ])
    _write_audit(data_dir, "job2", [
        "banned phrase: 'leveraged'",
    ])
    _write_audit(data_dir, "job3", [])  # clean

    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "validators", "--window-days", "365"])
    assert result.exit_code == 0, result.output
    assert "banned_phrase" in result.output
    assert "unverified_number" in result.output
    # 2 / 3 audits had a banned_phrase fire — share should read 67%.
    assert "67%" in result.output or "66%" in result.output


def test_analyze_validators_no_audits_in_window(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    _seed_minimal_config(tmp_config_dir, data_dir)
    # Write an audit but backdate its mtime to 60 days ago.
    p = _write_audit(data_dir, "old-job", ["banned phrase: 'spearheaded'"])
    old_ts = time.time() - 60 * 86400
    os.utime(p, (old_ts, old_ts))

    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "validators", "--window-days", "30"])
    assert result.exit_code == 0
    assert "no audit files modified in the last 30d" in result.output


def test_analyze_validators_no_applications_dir(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _seed_minimal_config(tmp_config_dir, data_dir)
    # No applications/ subdir.
    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "validators"])
    assert result.exit_code == 0
    assert "no applications" in result.output


def test_analyze_validators_healthy_run(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    """All audits clean → 'Healthy!' message."""
    data_dir = tmp_path / "data"
    _seed_minimal_config(tmp_config_dir, data_dir)
    _write_audit(data_dir, "job1", [])
    _write_audit(data_dir, "job2", [])

    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "validators", "--window-days", "365"])
    assert result.exit_code == 0
    assert "Healthy" in result.output
