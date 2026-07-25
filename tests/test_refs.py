"""`commands._refs.resolve_job_ref` — shared job-reference resolution.

The `applied` scope is covered by the existing `tests/test_track_cmd.py` suite
(which still calls `track_cmd._resolve_ref` and so guards the extraction).
These tests focus on the `jobs` scope, which is new, and on the scope boundary.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import typer

from jobhunt.commands._refs import resolve_job_ref
from jobhunt.db import migrate


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    repo_root = Path(__file__).resolve().parents[1]
    c = sqlite3.connect(tmp_path / "t.db")
    c.row_factory = sqlite3.Row
    migrate(c, repo_root / "migrations")
    with c:
        c.execute(
            "INSERT INTO jobs (id, source, external_id, company, title) "
            "VALUES ('greenhouse:faire:1', 'greenhouse', '1', 'Faire', 'Engineer')"
        )
        c.execute(
            "INSERT INTO jobs (id, source, external_id, company, title) "
            "VALUES ('adzuna_ca:2', 'adzuna_ca', '2', 'Globex', 'Frontend Developer')"
        )
        c.execute(
            "INSERT INTO jobs (id, source, external_id, company, title) "
            "VALUES ('adzuna_ca:3', 'adzuna_ca', '3', 'Initech', 'Backend Developer')"
        )
        # Declined — must not be reachable by fragment in the jobs scope.
        c.execute(
            "INSERT INTO jobs (id, source, external_id, company, title, decline_reason) "
            "VALUES ('adzuna_ca:4', 'adzuna_ca', '4', 'Umbrella', 'Engineer', 'wrong_domain')"
        )
        # Only Faire has been applied to.
        c.execute(
            "INSERT INTO applications (id, job_id, status) "
            "VALUES ('a1', 'greenhouse:faire:1', 'applied')"
        )
    return c


def test_jobs_scope_exact_id(conn: sqlite3.Connection) -> None:
    assert resolve_job_ref(conn, "adzuna_ca:2", scope="jobs") == "adzuna_ca:2"


def test_jobs_scope_resolves_fragment_for_unapplied_job(conn: sqlite3.Connection) -> None:
    """The whole point of the scope: Globex has no application row."""
    assert resolve_job_ref(conn, "globex", scope="jobs") == "adzuna_ca:2"


def test_jobs_scope_matches_title_fragment(conn: sqlite3.Connection) -> None:
    assert resolve_job_ref(conn, "frontend", scope="jobs") == "adzuna_ca:2"


def test_jobs_scope_is_case_insensitive(conn: sqlite3.Connection) -> None:
    assert resolve_job_ref(conn, "INITECH", scope="jobs") == "adzuna_ca:3"


def test_jobs_scope_ambiguous_lists_candidates(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(typer.Exit) as e:
        resolve_job_ref(conn, "developer", scope="jobs")
    assert e.value.exit_code == 1
    err = capsys.readouterr().err
    assert "ambiguous" in err
    assert "adzuna_ca:2" in err
    assert "adzuna_ca:3" in err


def test_jobs_scope_skips_declined(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """A declined posting should read as 'no match', not resolve."""
    with pytest.raises(typer.Exit):
        resolve_job_ref(conn, "umbrella", scope="jobs")
    assert "no job matches" in capsys.readouterr().err


def test_applied_scope_excludes_unapplied_job(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scope boundary: Globex resolves under `jobs` but not under `applied`."""
    assert resolve_job_ref(conn, "globex", scope="jobs") == "adzuna_ca:2"
    with pytest.raises(typer.Exit):
        resolve_job_ref(conn, "globex", scope="applied")
    assert "no tracked application" in capsys.readouterr().err


def test_applied_scope_resolves_applied_job(conn: sqlite3.Connection) -> None:
    assert resolve_job_ref(conn, "faire", scope="applied") == "greenhouse:faire:1"


def test_unknown_scope_is_a_programming_error(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        resolve_job_ref(conn, "faire", scope="nonsense")
