from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jobhunt.commands.db_cmd import _reset_targets


def _stub_cfg(root: Path) -> SimpleNamespace:
    data_dir = root / "data"
    return SimpleNamespace(
        paths=SimpleNamespace(
            db_path=data_dir / "jobhunt.db",
            data_dir=data_dir,
            kb_dir=root / "kb",
        ),
        browser=SimpleNamespace(user_data_dir=data_dir / "browser-profile"),
    )


def test_reset_targets_include_interview_prep_and_answers(tmp_path: Path) -> None:
    cfg = _stub_cfg(tmp_path)
    targets = _reset_targets(cfg)  # type: ignore[arg-type]

    data_dir = tmp_path / "data"
    # The two dirs this change adds.
    assert data_dir / "interview-prep" in targets
    assert data_dir / "answers" in targets
    # Regression: the pre-existing targets are still wiped.
    assert data_dir / "applications" in targets
    assert data_dir / "cache" in targets
    assert data_dir / "jobhunt.db" in targets
    assert tmp_path / "kb" / "profile" in targets
    assert data_dir / "browser-profile" in targets
