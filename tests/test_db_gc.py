"""`jobhunt db gc` — reconcile data/applications/ against the applications table.

Covers the classification taxonomy that drives every destructive decision:
a `block`-verdict dir is NOT an orphan (no row is the correct state for it),
a dir holding rendered .docx is recoverable work, and a docless dir is litter.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jobhunt.commands import db_cmd
from jobhunt.config import Config
from jobhunt.db import migrate


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    repo_root = Path(__file__).resolve().parents[1]
    cfg = Config()
    cfg.paths.data_dir = tmp_path
    cfg.paths.db_path = tmp_path / "jobhunt.db"
    cfg.paths.migrations_dir = repo_root / "migrations"
    conn = sqlite3.connect(cfg.paths.db_path)
    conn.row_factory = sqlite3.Row
    try:
        migrate(conn, cfg.paths.migrations_dir)
        with conn:
            for jid in ("adzuna_ca:1", "adzuna_ca:2", "adzuna_ca:3", "adzuna_ca:4"):
                conn.execute(
                    "INSERT INTO jobs (id, source, external_id, company, title) "
                    "VALUES (?, 'adzuna_ca', ?, 'ACME', 'Engineer')",
                    (jid, jid.split(":")[1]),
                )
            # adzuna_ca:4 is the one that already has an application row.
            conn.execute(
                "INSERT INTO applications (id, job_id, status) "
                "VALUES ('a4', 'adzuna_ca:4', 'applied')"
            )
    finally:
        conn.close()
    return cfg


def _mkdir(cfg: Config, name: str, *, files: dict[str, str]) -> Path:
    d = Path(cfg.paths.data_dir) / "applications" / name
    d.mkdir(parents=True)
    for fname, body in files.items():
        (d / fname).write_text(body, encoding="utf-8")
    return d


def _seed(cfg: Config) -> None:
    # adoptable: rendered docs, no row
    _mkdir(cfg, "adzuna_ca_1", files={
        "audit.json": json.dumps({"verdict": "ship"}),
        "Casey_Hsu_Resume.docx": "x",
        "Casey_Hsu_Cover_Letter.docx": "x",
        "fill-plan.json": "{}",
    })
    # stale: audit only, verdict ship — died before render
    _mkdir(cfg, "adzuna_ca_2", files={"audit.json": json.dumps({"verdict": "ship"})})
    # blocked: correct state, must never be actioned
    _mkdir(cfg, "adzuna_ca_3", files={"audit.json": json.dumps({"verdict": "block"})})
    # tracked: has an application row, not an orphan at all
    _mkdir(cfg, "adzuna_ca_4", files={"audit.json": json.dumps({"verdict": "ship"})})


def _classify(cfg: Config) -> dict[str, str]:
    conn = sqlite3.connect(cfg.paths.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return {o.path.name: o.kind for o in db_cmd._classify_orphans(cfg, conn)}
    finally:
        conn.close()


def test_classify_separates_adoptable_stale_and_blocked(cfg: Config) -> None:
    _seed(cfg)
    kinds = _classify(cfg)
    assert kinds == {
        "adzuna_ca_1": "adoptable",
        "adzuna_ca_2": "stale",
        "adzuna_ca_3": "blocked",
    }
    # The dir with a row is absent entirely — not an orphan.
    assert "adzuna_ca_4" not in kinds


def test_empty_dir_is_stale(cfg: Config) -> None:
    _mkdir(cfg, "adzuna_ca_1", files={})
    assert _classify(cfg)["adzuna_ca_1"] == "stale"


def test_adopt_writes_drafted_row_dated_from_dir_mtime(cfg: Config) -> None:
    _seed(cfg)
    conn = sqlite3.connect(cfg.paths.db_path)
    conn.row_factory = sqlite3.Row
    try:
        orphans = db_cmd._classify_orphans(cfg, conn)
        adoptable = next(o for o in orphans if o.kind == "adoptable")
        assert db_cmd._adopt(conn, adoptable) is True
        conn.commit()
        row = conn.execute(
            "SELECT * FROM applications WHERE job_id = 'adzuna_ca:1'"
        ).fetchone()
    finally:
        conn.close()

    assert row["status"] == "drafted"
    assert row["resume_path"].endswith("Casey_Hsu_Resume.docx")
    assert row["cover_path"].endswith("Casey_Hsu_Cover_Letter.docx")
    assert row["fill_plan_path"].endswith("fill-plan.json")
    # Backfill must not be dated today — it carries the dir's own week.
    assert row["applied_week"].startswith("20")
    assert row["applied_at"] is None  # drafted, never submitted


def test_adopt_skips_orphan_whose_job_row_is_gone(cfg: Config) -> None:
    _mkdir(cfg, "adzuna_ca_99", files={"Casey_Hsu_Resume.docx": "x"})
    conn = sqlite3.connect(cfg.paths.db_path)
    conn.row_factory = sqlite3.Row
    try:
        orphan = next(
            o for o in db_cmd._classify_orphans(cfg, conn) if o.path.name == "adzuna_ca_99"
        )
        assert orphan.kind == "adoptable"
        assert orphan.job_id is None
        assert db_cmd._adopt(conn, orphan) is False
        assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1
    finally:
        conn.close()


def test_blocked_dir_is_never_pruned_or_adopted(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing safety property: a block verdict is correct state."""
    _seed(cfg)
    monkeypatch.setattr(db_cmd, "load_config", lambda: cfg)

    from typer.testing import CliRunner

    result = CliRunner().invoke(db_cmd.app, ["gc", "--adopt", "--prune", "--force"])
    assert result.exit_code == 0, result.output

    apps_dir = Path(cfg.paths.data_dir) / "applications"
    assert (apps_dir / "adzuna_ca_3").is_dir()  # blocked survives
    assert (apps_dir / "adzuna_ca_1").is_dir()  # adopted, not deleted
    assert not (apps_dir / "adzuna_ca_2").exists()  # stale pruned

    conn = sqlite3.connect(cfg.paths.db_path)
    try:
        ids = {r[0] for r in conn.execute("SELECT job_id FROM applications")}
    finally:
        conn.close()
    assert ids == {"adzuna_ca:4", "adzuna_ca:1"}
    assert "adzuna_ca:3" not in ids  # blocked never gets a row


def test_bare_gc_is_a_dry_run(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(cfg)
    monkeypatch.setattr(db_cmd, "load_config", lambda: cfg)

    from typer.testing import CliRunner

    result = CliRunner().invoke(db_cmd.app, ["gc"])
    assert result.exit_code == 0, result.output
    assert "dry run" in result.output

    apps_dir = Path(cfg.paths.data_dir) / "applications"
    assert (apps_dir / "adzuna_ca_2").is_dir()  # nothing deleted
    conn = sqlite3.connect(cfg.paths.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1
    finally:
        conn.close()
