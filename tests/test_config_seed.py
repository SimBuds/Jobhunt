from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from jobhunt.cli import app


def _seed_config_and_kb(
    config_dir: Path, seeds: dict[str, list[str]], **overrides: list[str]
) -> tuple[Path, Path]:
    """Write config.toml + a kb/seeds/gta-employers.toml under tmp dirs.
    Returns (config_path, seed_path)."""
    jh_dir = config_dir / "jobhunt"
    jh_dir.mkdir(parents=True, exist_ok=True)
    kb_dir = config_dir / "kb"

    cfg_path = jh_dir / "config.toml"
    ingest_lines = []
    for ats in ("greenhouse", "lever", "ashby", "smartrecruiters", "workday"):
        entries = overrides.get(ats, [])
        joined = ", ".join(f'"{e}"' for e in entries)
        ingest_lines.append(f"{ats} = [{joined}]")
    cfg_path.write_text(
        "[paths]\n"
        f'kb_dir = "{kb_dir}"\n'
        "[ingest]\n" + "\n".join(ingest_lines) + "\n"
    )

    seed_path = kb_dir / "seeds" / "gta-employers.toml"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_lines = []
    for ats, entries in seeds.items():
        joined = ", ".join(f'"{e}"' for e in entries)
        seed_lines.append(f"{ats} = [{joined}]")
    seed_path.write_text("\n".join(seed_lines) + "\n")
    return cfg_path, seed_path


def _load_ingest(cfg_path: Path) -> dict[str, Any]:
    with cfg_path.open("rb") as f:
        return tomllib.load(f)["ingest"]


def test_config_seed_preview_does_not_write(tmp_config_dir: Path) -> None:
    cfg_path, _ = _seed_config_and_kb(
        tmp_config_dir, seeds={"greenhouse": ["shopify", "faire"]}
    )
    original = cfg_path.read_bytes()

    runner = CliRunner()
    result = runner.invoke(app, ["config", "seed", "--preview"])

    assert result.exit_code == 0, result.output
    assert "shopify" in result.output
    assert "faire" in result.output
    # No write
    assert cfg_path.read_bytes() == original


def test_config_seed_apply_writes_additively(tmp_config_dir: Path) -> None:
    cfg_path, _ = _seed_config_and_kb(
        tmp_config_dir,
        seeds={"greenhouse": ["shopify", "faire"], "ashby": ["cohere"]},
        greenhouse=["braze"],  # already configured
    )

    runner = CliRunner()
    result = runner.invoke(app, ["config", "seed", "--apply"])

    assert result.exit_code == 0, result.output
    ingest = _load_ingest(cfg_path)
    assert ingest["greenhouse"] == ["braze", "shopify", "faire"]
    assert ingest["ashby"] == ["cohere"]


def test_config_seed_apply_idempotent(tmp_config_dir: Path) -> None:
    cfg_path, _ = _seed_config_and_kb(
        tmp_config_dir,
        seeds={"greenhouse": ["shopify"]},
        greenhouse=["shopify"],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["config", "seed", "--apply"])

    assert result.exit_code == 0
    assert "nothing to add" in result.output
    assert _load_ingest(cfg_path)["greenhouse"] == ["shopify"]


def test_config_seed_apply_creates_bak(tmp_config_dir: Path) -> None:
    cfg_path, _ = _seed_config_and_kb(
        tmp_config_dir, seeds={"greenhouse": ["shopify"]}
    )
    original = cfg_path.read_bytes()

    runner = CliRunner()
    result = runner.invoke(app, ["config", "seed", "--apply"])

    assert result.exit_code == 0, result.output
    bak = cfg_path.with_suffix(cfg_path.suffix + ".bak")
    assert bak.exists()
    assert bak.read_bytes() == original


def test_config_seed_requires_a_flag(tmp_config_dir: Path) -> None:
    _seed_config_and_kb(tmp_config_dir, seeds={"greenhouse": ["shopify"]})

    runner = CliRunner()
    result = runner.invoke(app, ["config", "seed"])

    assert result.exit_code != 0
    assert "preview" in result.output.lower() or "apply" in result.output.lower()
