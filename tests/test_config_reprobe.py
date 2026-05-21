"""Phase 7 tests — `jobhunt config reprobe` subcommand."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from jobhunt.cli import app
from jobhunt.discover import probe as probe_mod
from jobhunt.discover.probe import ProbeOutcome


def _seed_config(config_dir: Path, **overrides: list[str]) -> Path:
    """Write a minimal config.toml with seeded slugs."""
    jh_dir = config_dir / "jobhunt"
    jh_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = jh_dir / "config.toml"

    ingest_lines = []
    for ats in ("greenhouse", "lever", "ashby", "smartrecruiters", "workday"):
        entries = overrides.get(ats, [])
        joined = ", ".join(f'"{e}"' for e in entries)
        ingest_lines.append(f"{ats} = [{joined}]")
    cfg_path.write_text(
        "[paths]\n"
        f'kb_dir = "{config_dir / "kb"}"\n'
        "[ingest]\n"
        + "\n".join(ingest_lines)
        + "\n"
    )
    profile = config_dir / "kb" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "verified.json").write_text("{}")
    return cfg_path


def _load_ingest(cfg_path: Path) -> dict[str, Any]:
    with cfg_path.open("rb") as f:
        return tomllib.load(f)["ingest"]


def _outcomes(per_ats: dict[str, dict[str, ProbeOutcome]]):
    """Stub `_probe_one` so it returns a pre-canned outcome per (ats, slug)."""
    async def fake(
        client: Any, limiter: Any, company: str, ats: str, slug: str
    ) -> ProbeOutcome:
        return per_ats.get(ats, {}).get(
            slug, ProbeOutcome(company, ats, slug, 0, None)
        )
    return fake


def test_reprobe_dry_run_prints_stale_without_writing(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _seed_config(
        tmp_config_dir,
        greenhouse=["live-co", "stale-co"],
        lever=["another-live"],
    )
    monkeypatch.setattr(probe_mod, "_probe_one", _outcomes({
        "greenhouse": {
            "live-co": ProbeOutcome("live-co", "greenhouse", "live-co", 200, 12),
            "stale-co": ProbeOutcome("stale-co", "greenhouse", "stale-co", 404, None),
        },
        "lever": {
            "another-live": ProbeOutcome("another-live", "lever", "another-live", 200, 5),
        },
    }))

    runner = CliRunner()
    result = runner.invoke(app, ["config", "reprobe"])
    assert result.exit_code == 0, result.output
    assert "live  live-co" in result.output
    assert "STALE stale-co" in result.output
    assert "live  another-live" in result.output
    # Dry run — must not have written.
    after = _load_ingest(cfg_path)
    assert after["greenhouse"] == ["live-co", "stale-co"]


def test_reprobe_prune_with_force_removes_stale(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _seed_config(
        tmp_config_dir,
        greenhouse=["live-co", "stale-co", "another-stale"],
        ashby=["good-ash"],
    )
    monkeypatch.setattr(probe_mod, "_probe_one", _outcomes({
        "greenhouse": {
            "live-co": ProbeOutcome("live-co", "greenhouse", "live-co", 200, 12),
            "stale-co": ProbeOutcome("stale-co", "greenhouse", "stale-co", 404, None),
            "another-stale": ProbeOutcome("another-stale", "greenhouse", "another-stale", 0, None),
        },
        "ashby": {
            "good-ash": ProbeOutcome("good-ash", "ashby", "good-ash", 200, 4),
        },
    }))

    runner = CliRunner()
    result = runner.invoke(app, ["config", "reprobe", "--prune", "--force"])
    assert result.exit_code == 0, result.output
    after = _load_ingest(cfg_path)
    assert after["greenhouse"] == ["live-co"]
    assert after["ashby"] == ["good-ash"]


def test_reprobe_prune_aborts_without_confirmation(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _seed_config(
        tmp_config_dir, greenhouse=["live-co", "stale-co"]
    )
    monkeypatch.setattr(probe_mod, "_probe_one", _outcomes({
        "greenhouse": {
            "live-co": ProbeOutcome("live-co", "greenhouse", "live-co", 200, 1),
            "stale-co": ProbeOutcome("stale-co", "greenhouse", "stale-co", 404, None),
        },
    }))

    runner = CliRunner()
    # Stdin "n\n" → typer.confirm returns False
    result = runner.invoke(app, ["config", "reprobe", "--prune"], input="n\n")
    assert result.exit_code == 1
    assert "aborted" in result.output
    # Config untouched.
    after = _load_ingest(cfg_path)
    assert "stale-co" in after["greenhouse"]


def test_reprobe_with_no_stale_does_nothing(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_config(tmp_config_dir, greenhouse=["live-co"])
    monkeypatch.setattr(probe_mod, "_probe_one", _outcomes({
        "greenhouse": {
            "live-co": ProbeOutcome("live-co", "greenhouse", "live-co", 200, 3),
        },
    }))

    runner = CliRunner()
    result = runner.invoke(app, ["config", "reprobe", "--prune", "--force"])
    assert result.exit_code == 0
    assert "nothing to prune" in result.output


def test_reprobe_with_empty_config_exits_cleanly(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_config(tmp_config_dir)  # all empty
    monkeypatch.setattr(probe_mod, "_probe_one", _outcomes({}))

    runner = CliRunner()
    result = runner.invoke(app, ["config", "reprobe"])
    assert result.exit_code == 0
    assert "no configured slugs" in result.output


def test_reprobe_force_without_prune_rejects(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_config(tmp_config_dir, greenhouse=["live-co"])
    monkeypatch.setattr(probe_mod, "_probe_one", _outcomes({}))

    runner = CliRunner()
    result = runner.invoke(app, ["config", "reprobe", "--force"])
    assert result.exit_code != 0
    assert "--force only applies with --prune" in result.output


def test_reprobe_skips_workday(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workday tenants are specs, not raw slugs, and lack a probeable
    public endpoint. They must not be passed to _probe_one."""
    _seed_config(
        tmp_config_dir,
        greenhouse=["live-co"],
        workday=["rbc:wd3:RBC_Careers"],
    )
    calls: list[str] = []

    async def fake(
        client: Any, limiter: Any, company: str, ats: str, slug: str
    ) -> ProbeOutcome:
        calls.append(ats)
        return ProbeOutcome(company, ats, slug, 200, 1)

    monkeypatch.setattr(probe_mod, "_probe_one", fake)
    runner = CliRunner()
    result = runner.invoke(app, ["config", "reprobe"])
    assert result.exit_code == 0
    assert "workday" not in calls, f"workday slug was probed: {calls}"
