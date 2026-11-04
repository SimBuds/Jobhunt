from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jobhunt.config import ApplicantProfile, Config, config_path, load_config


def test_default_config_writes_and_loads(tmp_config_dir: Path) -> None:
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert config_path().exists()
    # Second load should not raise.
    cfg2 = load_config()
    assert cfg2.gateway.base_url == cfg.gateway.base_url


def test_env_var_override(tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBHUNT_GATEWAY__BASE_URL", "http://example.test:1234/v1")
    cfg = load_config()
    assert cfg.gateway.base_url == "http://example.test:1234/v1"


def test_example_toml_is_parseable(tmp_config_dir: Path) -> None:
    text = Config.example_toml()
    assert "[gateway]" in text
    assert "[paths]" in text


def test_applicant_profile_defaults() -> None:
    p = ApplicantProfile()
    assert p.work_arrangements == ["onsite", "hybrid", "remote"]
    assert p.employment_types == ["full_time", "contract"]
    assert p.portfolio_url == ""


def test_applicant_profile_overrides_round_trip() -> None:
    cfg = Config.model_validate(
        {
            "applicant": {
                "salary_expectation_cad": "50,000 - 90,000 CAD",
                "work_arrangements": ["hybrid", "remote"],
                "employment_types": ["contract"],
            }
        }
    )
    assert cfg.applicant.salary_expectation_cad == "50,000 - 90,000 CAD"
    assert cfg.applicant.work_arrangements == ["hybrid", "remote"]
    assert cfg.applicant.employment_types == ["contract"]


def test_applicant_profile_rejects_invalid_literal() -> None:
    with pytest.raises(ValidationError):
        ApplicantProfile(employment_types=["casual"])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        ApplicantProfile(work_arrangements=["anywhere"])  # type: ignore[list-item]


def test_invalid_toml_raises(tmp_config_dir: Path) -> None:
    from jobhunt.errors import ConfigError

    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("not = valid = toml")
    with pytest.raises(ConfigError):
        load_config(write_default_if_missing=False)


def test_example_toml_leaves_working_dir_paths_out(tmp_config_dir: Path) -> None:
    """Phase 22j (2026-11-04): the first-run config used to pin kb_dir and
    migrations_dir to whatever directory jobhunt was first run from. From a
    wrong directory that baked the wrong paths in, and the working-dir check
    then skipped them as user-set. Private paths are still written."""
    import tomllib

    paths = tomllib.loads(Config.example_toml())["paths"]
    assert "kb_dir" not in paths
    assert "migrations_dir" not in paths
    assert {"data_dir", "db_path", "profile_dir"} <= paths.keys()
