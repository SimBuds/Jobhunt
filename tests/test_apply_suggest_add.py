from __future__ import annotations

import typer

from jobhunt.commands.apply_cmd import _maybe_suggest_add
from jobhunt.config import Config


def _cfg(**ingest_overrides: list[str]) -> Config:
    cfg = Config()
    for ats, slugs in ingest_overrides.items():
        setattr(cfg.ingest, ats, slugs)
    return cfg


def test_suggest_fires_for_unknown_greenhouse_slug(capsys: typer.testing.CliRunner) -> None:  # type: ignore[name-defined]
    _maybe_suggest_add(_cfg(), "https://boards.greenhouse.io/braze/jobs/123")
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "greenhouse" in out
    assert "'braze'" in out
    assert "jobhunt add" in out


def test_suggest_suppressed_when_already_configured(capsys) -> None:  # type: ignore[no-untyped-def]
    _maybe_suggest_add(
        _cfg(greenhouse=["braze"]),
        "https://boards.greenhouse.io/braze/jobs/123",
    )
    assert capsys.readouterr().out == ""


def test_suggest_suppressed_for_icims(capsys) -> None:  # type: ignore[no-untyped-def]
    _maybe_suggest_add(
        _cfg(),
        "https://careers-acme.icims.com/jobs/123",
    )
    assert capsys.readouterr().out == ""


def test_suggest_suppressed_for_unrecognized_url(capsys) -> None:  # type: ignore[no-untyped-def]
    _maybe_suggest_add(_cfg(), "https://example.com/jobs/123")
    assert capsys.readouterr().out == ""


def test_suggest_fires_for_workday_with_full_path(capsys) -> None:  # type: ignore[no-untyped-def]
    _maybe_suggest_add(
        _cfg(),
        "https://rbc.wd3.myworkdayjobs.com/en-US/RBC_Careers/job/Toronto/Dev",
    )
    out = capsys.readouterr().out
    assert "workday" in out
    assert "rbc:wd3:RBC_Careers" in out


def test_suggest_suppressed_for_workday_missing_site(capsys) -> None:  # type: ignore[no-untyped-def]
    # Bare tenant URL with no site segment — we can't build a complete config
    # value, so don't nudge.
    _maybe_suggest_add(_cfg(), "https://rbc.wd3.myworkdayjobs.com/")
    assert capsys.readouterr().out == ""
