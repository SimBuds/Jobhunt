"""Phase 12 tests — answer index + recall."""
from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.db import connect, migrate
from jobhunt.pipeline._answer_index import (
    backfill_existing,
    index_answer,
    question_sha1,
    recall,
)


@pytest.fixture
def conn(tmp_path: Path, migrations_dir: Path):
    c = connect(tmp_path / "test.db")
    migrate(c, migrations_dir)
    yield c
    c.close()


def _write_artifact(dir_: Path, sha: str, question: str, answer: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / f"{sha}.md"
    p.write_text(
        f"# Question\n\n{question.strip()}\n\n# Answer\n\n{answer.strip()}\n",
        encoding="utf-8",
    )
    return p


# --- core helpers -----------------------------------------------------------


def test_question_sha1_is_stable_12_chars() -> None:
    assert len(question_sha1("Why this role?")) == 12
    # Whitespace matters — the on-disk filename does not normalise.
    assert question_sha1("Why this role?") == question_sha1("Why this role?")
    assert question_sha1("Why this role?") != question_sha1("Why this role")


def test_index_answer_standalone(conn, tmp_path: Path) -> None:
    p = tmp_path / "answers" / "abc.md"
    p.parent.mkdir()
    p.write_text("...")
    with conn:
        index_answer(
            conn, sha1="abc12345", question="Why us?", job_id=None, path=p
        )
    row = conn.execute("SELECT job_id, question FROM answers").fetchone()
    # Standalone stores '' in job_id.
    assert row["job_id"] == ""
    assert row["question"] == "Why us?"


def test_index_answer_job_scoped(conn, tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("...")
    with conn:
        index_answer(
            conn, sha1="def67890", question="React?", job_id="adzuna_5731", path=p
        )
    row = conn.execute("SELECT job_id FROM answers").fetchone()
    assert row["job_id"] == "adzuna_5731"


def test_index_answer_upsert_overwrites_path(conn, tmp_path: Path) -> None:
    p1 = tmp_path / "v1.md"
    p1.write_text(".")
    p2 = tmp_path / "v2.md"
    p2.write_text(".")
    with conn:
        index_answer(conn, sha1="x", question="q", job_id=None, path=p1)
    with conn:
        index_answer(conn, sha1="x", question="q updated", job_id=None, path=p2)
    rows = conn.execute("SELECT question, path FROM answers").fetchall()
    assert len(rows) == 1
    assert rows[0]["question"] == "q updated"
    assert rows[0]["path"] == str(p2)


def test_same_sha1_two_job_scopes_distinct(conn, tmp_path: Path) -> None:
    """The same question saved standalone AND under a specific job must
    produce two rows — they're separate artifacts."""
    p = tmp_path / "a.md"
    p.write_text(".")
    with conn:
        index_answer(conn, sha1="x", question="q", job_id=None, path=p)
        index_answer(conn, sha1="x", question="q", job_id="job-1", path=p)
    rows = conn.execute("SELECT job_id FROM answers ORDER BY job_id").fetchall()
    assert {r["job_id"] for r in rows} == {"", "job-1"}


# --- recall -----------------------------------------------------------------


def test_recall_substring_match_case_insensitive(conn, tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text(".")
    with conn:
        index_answer(
            conn, sha1="1", question="Why are you leaving your current role?",
            job_id=None, path=p,
        )
        index_answer(
            conn, sha1="2", question="Tell me about a conflict",
            job_id=None, path=p,
        )
    hits = recall(conn, "leaving")
    assert len(hits) == 1
    assert "leaving" in hits[0]["question"].lower()


def test_recall_returns_no_rows_when_nothing_matches(conn) -> None:
    assert recall(conn, "nonexistent") == []


def test_recall_normalises_empty_job_id_to_none(conn, tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text(".")
    with conn:
        index_answer(conn, sha1="x", question="q standalone", job_id=None, path=p)
    hits = recall(conn, "standalone")
    assert hits[0]["job_id"] is None


# --- backfill ---------------------------------------------------------------


def test_backfill_standalone_and_job_scoped(conn, tmp_path: Path) -> None:
    _write_artifact(
        tmp_path / "answers", "aaaa11112222", "Standalone question?", "ans"
    )
    _write_artifact(
        tmp_path / "applications" / "adzuna_ca_5731" / "answers",
        "bbbb33334444",
        "Job-scoped question?",
        "ans",
    )
    updated = backfill_existing(conn, tmp_path)
    assert updated == 2

    rows = conn.execute(
        "SELECT job_id, question FROM answers ORDER BY question"
    ).fetchall()
    questions = [r["question"] for r in rows]
    assert "Standalone question?" in questions
    assert "Job-scoped question?" in questions


def test_backfill_skips_malformed_files(conn, tmp_path: Path) -> None:
    """Files that don't match the # Question / # Answer template are skipped
    silently so the backfill doesn't crash on user-edited artifacts."""
    d = tmp_path / "answers"
    d.mkdir()
    (d / "garbage.md").write_text("just some notes, not an answer")
    updated = backfill_existing(conn, tmp_path)
    assert updated == 0


def test_backfill_is_idempotent(conn, tmp_path: Path) -> None:
    _write_artifact(
        tmp_path / "answers", "abcd00001111", "Why?", "Because."
    )
    backfill_existing(conn, tmp_path)
    backfill_existing(conn, tmp_path)
    rows = conn.execute("SELECT COUNT(*) AS n FROM answers").fetchone()
    assert rows["n"] == 1
