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
    """Write a minimal config.toml under the redirected XDG_CONFIG_HOME."""
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
    # Satisfy ensure_profile()
    profile = config_dir / "kb" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "verified.json").write_text("{}")
    return cfg_path


def _load_ingest(cfg_path: Path) -> dict[str, Any]:
    with cfg_path.open("rb") as f:
        return tomllib.load(f)["ingest"]


def _stub_probe_hit(monkeypatch: pytest.MonkeyPatch, count: int = 1) -> None:
    async def fake(client: Any, limiter: Any, company: str, ats: str, slug: str) -> ProbeOutcome:
        return ProbeOutcome(company, ats, slug, 200, count)

    monkeypatch.setattr(probe_mod, "_probe_one", fake)


def test_add_greenhouse_url_appends_slug(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _seed_config(tmp_config_dir)
    _stub_probe_hit(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["add", "https://boards.greenhouse.io/braze/jobs/123"])

    assert result.exit_code == 0, result.output
    assert "added: [ingest.greenhouse] 'braze'" in result.output
    assert _load_ingest(cfg_path)["greenhouse"] == ["braze"]


def test_add_workday_url_writes_tenant_host_site(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _seed_config(tmp_config_dir)
    # Workday path skips probing — no stub needed.

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["add", "https://rbc.wd3.myworkdayjobs.com/en-US/RBC_Careers/job/Toronto/Dev"],
    )

    assert result.exit_code == 0, result.output
    assert _load_ingest(cfg_path)["workday"] == ["rbc:wd3:RBC_Careers"]


def test_add_unknown_host_exits_with_message(tmp_config_dir: Path) -> None:
    cfg_path = _seed_config(tmp_config_dir)
    before = _load_ingest(cfg_path)

    runner = CliRunner()
    result = runner.invoke(app, ["add", "https://example.com/jobs/123"])

    assert result.exit_code == 1
    assert "didn't recognize this URL" in result.output
    assert _load_ingest(cfg_path) == before


def test_add_icims_url_rejected_as_unsupported(tmp_config_dir: Path) -> None:
    cfg_path = _seed_config(tmp_config_dir)
    before = _load_ingest(cfg_path)

    runner = CliRunner()
    result = runner.invoke(app, ["add", "https://careers-acme.icims.com/jobs/123"])

    assert result.exit_code == 2
    assert "icims support coming soon" in result.output
    assert _load_ingest(cfg_path) == before


def test_add_already_configured_is_noop(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _seed_config(tmp_config_dir, greenhouse=["braze"])
    _stub_probe_hit(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["add", "https://boards.greenhouse.io/braze"])

    assert result.exit_code == 0
    assert "already configured" in result.output
    # No duplicate appended.
    assert _load_ingest(cfg_path)["greenhouse"] == ["braze"]


def test_add_writes_bak_snapshot(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _seed_config(tmp_config_dir)
    original = cfg_path.read_bytes()
    _stub_probe_hit(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["add", "https://boards.greenhouse.io/braze"])

    assert result.exit_code == 0, result.output
    bak = cfg_path.with_suffix(cfg_path.suffix + ".bak")
    assert bak.exists()
    assert bak.read_bytes() == original
