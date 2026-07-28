"""Score breakdowns (migration 0010) and the coverage calibration they enable.

The final integer alone is ambiguous: two jobs at 70 may be a full-coverage
snippet pulled down by the thin-JD ceiling and a genuine two-thirds match,
which are not the same bet. Recording the components lets `config calibrate`
group by tier-1 coverage — the signal the weights actually control — instead of
by a number the ceilings may have set.

Rows scored before this migration read back NULL. That is honest and must stay
distinguishable from zero coverage: a missing measurement is not a bad one.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jobhunt.commands.config_cmd import _echo_coverage_calibration, _tier1_coverage
from jobhunt.db import connect, migrate, write_score
from jobhunt.pipeline.score import ScoreBreakdown

REPO_ROOT = Path(__file__).resolve().parent.parent


def _breakdown(**over: object) -> ScoreBreakdown:
    base: dict = {
        "tier1_matched": 3,
        "tier1_total": 4,
        "tier1_credit": 2.7,
        "tier2_matched": 1,
        "tier2_total": 2,
        "tier2_credit": 1.0,
        "ai_bonus": True,
        "computed": 80,
        "final": 70,
        "caps_applied": ["thin_jd"],
        "weights": {"base": 30, "tier1": 50, "tier2": 10, "ai_bonus": 5,
                    "transferable_credit": 0.7},
    }
    base.update(over)
    return ScoreBreakdown(**base)  # type: ignore[arg-type]


class TestBreakdownShape:
    def test_round_trips_through_json(self) -> None:
        data = json.loads(_breakdown().to_json())
        assert data["tier1"] == {"matched": 3, "total": 4, "credit": 2.7}
        assert data["computed"] == 80
        assert data["final"] == 70
        assert data["caps_applied"] == ["thin_jd"]

    def test_records_the_pre_cap_score_too(self) -> None:
        """`computed` vs `final` is the whole point: without the pre-cap value
        a capped score is indistinguishable from an earned one."""
        data = json.loads(_breakdown(computed=90, final=70).to_json())
        assert data["computed"] != data["final"]

    def test_tier1_coverage_is_graded(self) -> None:
        assert _breakdown(tier1_credit=2.7, tier1_total=4).tier1_coverage == 0.675

    def test_tier1_coverage_of_empty_tier_is_zero_not_a_crash(self) -> None:
        assert _breakdown(tier1_total=0, tier1_credit=0.0).tier1_coverage == 0.0


class TestMigration:
    """Migration 0010 is additive. It must not disturb existing score rows."""

    def test_applies_to_a_db_with_pre_existing_scores(self, tmp_path: Path) -> None:
        db = tmp_path / "jobhunt.db"
        conn = connect(db)
        # Migrate to the state *before* 0010, insert a score, then finish.
        mig = REPO_ROOT / "migrations"
        early = tmp_path / "early"
        early.mkdir()
        for p in sorted(mig.glob("0*.sql")):
            if p.name.startswith("0010"):
                continue
            (early / p.name).write_text(p.read_text())
        migrate(conn, early)
        conn.execute(
            "INSERT INTO jobs (id, source, external_id, company, title, description)"
            " VALUES ('t:1','test','1','Acme','Dev','jd')"
        )
        conn.execute(
            "INSERT INTO scores (job_id, score, model, prompt_hash)"
            " VALUES ('t:1', 82, 'qwen3.5:9b', 'abc')"
        )
        conn.commit()

        migrate(conn, mig)  # now apply 0010 over real data

        row = conn.execute("SELECT score, breakdown FROM scores").fetchone()
        assert row["score"] == 82, "existing score must survive untouched"
        assert row["breakdown"] is None, "pre-migration rows read back NULL"
        conn.close()

    def test_write_score_persists_and_reads_back(self, tmp_path: Path) -> None:
        db = tmp_path / "jobhunt.db"
        conn = connect(db)
        migrate(conn, REPO_ROOT / "migrations")
        conn.execute(
            "INSERT INTO jobs (id, source, external_id, company, title, description)"
            " VALUES ('t:1','test','1','Acme','Dev','jd')"
        )
        with conn:
            write_score(
                conn, job_id="t:1", score=70, reasons=["React"], red_flags=[],
                must_clarify=["Rust"], model="qwen3.5:9b", prompt_hash="abc",
                breakdown=_breakdown().to_json(),
            )
        stored = conn.execute("SELECT breakdown FROM scores").fetchone()["breakdown"]
        assert json.loads(stored)["tier1"]["total"] == 4
        conn.close()

    def test_breakdown_is_optional(self, tmp_path: Path) -> None:
        """Callers with no components must record NULL, never a fake zero."""
        db = tmp_path / "jobhunt.db"
        conn = connect(db)
        migrate(conn, REPO_ROOT / "migrations")
        conn.execute(
            "INSERT INTO jobs (id, source, external_id, company, title, description)"
            " VALUES ('t:1','test','1','Acme','Dev','jd')"
        )
        with conn:
            write_score(
                conn, job_id="t:1", score=70, reasons=[], red_flags=[],
                must_clarify=[], model="m", prompt_hash="abc",
            )
        assert conn.execute("SELECT breakdown FROM scores").fetchone()["breakdown"] is None
        conn.close()


class TestCoverageParsing:
    @pytest.mark.parametrize(
        "raw",
        [None, "", "not json", "{}", '{"tier1": {}}',
         '{"tier1": {"total": 0, "credit": 0}}',       # empty tier, not 0% fit
         '{"tier1": {"total": 4}}',                     # credit missing
         '{"tier1": {"total": "x", "credit": 1}}'],     # wrong type
    )
    def test_unusable_breakdowns_are_unknown_not_zero(self, raw: str | None) -> None:
        """Every one of these must be excluded from calibration, not counted as
        a 0% -coverage application, which would drag the bottom band down with
        rows that were never measured."""
        assert _tier1_coverage(raw) is None

    def test_parses_a_real_breakdown(self) -> None:
        assert _tier1_coverage(_breakdown().to_json()) == 0.675


class TestCalibrationOutput:
    def test_groups_by_coverage_not_by_capped_score(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two applications share score 70 but differ in real fit: one was a
        thin snippet capped down from 90, the other an honest 60%. The score
        table would put them in one band; the coverage table separates them."""
        rows = [
            {"score": 70, "status": "interviewing",
             "breakdown": _breakdown(tier1_matched=4, tier1_total=4,
                                     tier1_credit=4.0, computed=90,
                                     final=70).to_json()},
            {"score": 70, "status": "applied",
             "breakdown": _breakdown(tier1_matched=3, tier1_total=5,
                                     tier1_credit=3.0, computed=70,
                                     final=70).to_json()},
        ]
        _echo_coverage_calibration(rows, {"interviewing", "offer", "rejected"})
        out = capsys.readouterr().out
        lines = {ln.split()[0]: ln for ln in out.splitlines() if ln.strip()}
        # 4.0/4 = 100% and 3.0/5 = 60%; bands are [lo, hi) so 0.60 is the
        # bottom of 60-74%, not the top of "< 60%".
        assert lines["90-100%"].split()[1] == "1"
        assert lines["60-74%"].split()[1] == "1"
        # The point: identical final scores, different bands.
        assert lines["75-89%"].split()[1] == "0"
        assert lines["<"].split()[2] == "0"

    def test_reports_when_no_breakdowns_exist(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _echo_coverage_calibration(
            [{"score": 82, "status": "applied", "breakdown": None}], {"applied"}
        )
        assert "No score breakdowns recorded yet" in capsys.readouterr().out

    def test_counts_excluded_legacy_rows(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Legacy rows must be reported as excluded, not silently dropped —
        otherwise the sample size looks larger than it is."""
        rows = [
            {"score": 70, "status": "applied", "breakdown": _breakdown().to_json()},
            {"score": 82, "status": "applied", "breakdown": None},
            {"score": 58, "status": "applied", "breakdown": None},
        ]
        _echo_coverage_calibration(rows, {"interviewing"})
        assert "2 application(s) scored before breakdowns" in capsys.readouterr().out


def test_score_result_defaults_breakdown_to_none() -> None:
    """`apply_cmd._load_score` rebuilds a ScoreResult from DB columns and has
    no components to supply, so the field must stay optional."""
    from jobhunt.pipeline.score import ScoreResult

    r = ScoreResult(
        score=70, matched_must_haves=[], gaps=[], decline_reason=None,
        ai_bonus_present=False, model="m",
    )
    assert r.breakdown is None


def test_scores_table_has_breakdown_column(tmp_path: Path) -> None:
    db = tmp_path / "jobhunt.db"
    conn = connect(db)
    migrate(conn, REPO_ROOT / "migrations")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(scores)")}
    assert "breakdown" in cols
    conn.close()


def test_migration_is_additive_only() -> None:
    """0010 must never rewrite or drop existing data."""
    sql = (REPO_ROOT / "migrations" / "0010_score_breakdown.sql").read_text().upper()
    assert "ALTER TABLE SCORES ADD COLUMN" in sql
    for forbidden in ("DROP ", "DELETE ", "UPDATE ", "TRUNCATE"):
        assert forbidden not in sql, f"migration must not contain {forbidden.strip()}"


def test_sqlite_row_factory_is_available() -> None:
    """The calibration helper indexes rows by name."""
    assert sqlite3.Row is not None


def _pre_0010_db(tmp_path: Path) -> Path:
    """A database migrated to every migration EXCEPT 0010."""
    early = tmp_path / "early"
    early.mkdir()
    for p in sorted((REPO_ROOT / "migrations").glob("0*.sql")):
        if not p.name.startswith("0010"):
            (early / p.name).write_text(p.read_text())
    db = tmp_path / "jobhunt.db"
    conn = connect(db)
    migrate(conn, early)
    conn.close()
    return db


class TestPreMigrationDatabases:
    """Phase 4 must not break a database that has not been migrated yet.

    `scan` migrates on entry, but `apply --url` can be the first command run
    against a DB, and `calibrate` is read-only and must never migrate one out
    from under the user.
    """

    def test_write_score_with_breakdown_needs_the_migration(
        self, tmp_path: Path
    ) -> None:
        """Pins the failure this phase would otherwise have shipped, so the
        `apply` migrate call below cannot be removed without a red test."""
        db = _pre_0010_db(tmp_path)
        conn = connect(db)
        conn.execute(
            "INSERT INTO jobs (id, source, external_id, company, title, description)"
            " VALUES ('t:1','test','1','Acme','Dev','jd')"
        )
        with pytest.raises(sqlite3.OperationalError, match="breakdown"), conn:
            write_score(
                conn, job_id="t:1", score=70, reasons=[], red_flags=[],
                must_clarify=[], model="m", prompt_hash="h",
                breakdown=_breakdown().to_json(),
            )
        conn.close()

    def test_apply_cmd_migrates_before_scoring(self) -> None:
        """`apply` must migrate, like `scan` does. Asserted on the source
        because reaching the write path needs a full LLM+browser run."""
        src = (REPO_ROOT / "src" / "jobhunt" / "commands" / "apply_cmd.py").read_text()
        assert "migrate(conn, cfg.paths.migrations_dir)" in src

    def test_calibrate_query_survives_a_missing_column(self, tmp_path: Path) -> None:
        """The read-only path degrades instead of raising `no such column`."""
        db = _pre_0010_db(tmp_path)
        conn = connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(scores)")}
        assert "breakdown" not in cols, "fixture should predate 0010"
        breakdown_col = "s.breakdown" if "breakdown" in cols else "NULL AS breakdown"
        rows = list(
            conn.execute(
                f"SELECT s.score, {breakdown_col}, a.status FROM applications a "
                "JOIN scores s ON s.job_id = a.job_id"
            )
        )
        assert rows == []
        conn.close()
