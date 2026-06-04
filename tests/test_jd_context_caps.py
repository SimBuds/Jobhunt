"""P1 (context-budget initiative): the interview-prep and answer JD caps are
aligned to the score/tailor/cover budget (MAX_DESC_CHARS), so prep and answer
see the same JD scope the scoring path does. Before the bump these clipped at
6000 chars, truncating 63% of real JDs (DB sweep 2026-06-04, n=469)."""

from __future__ import annotations

from pathlib import Path

from jobhunt.commands.answer_cmd import _load_jd_context
from jobhunt.config import Config, PathsConfig
from jobhunt.db import connect, migrate, upsert_job
from jobhunt.models import Job
from jobhunt.pipeline.interview_prep import _JD_MAX_CHARS
from jobhunt.pipeline.score import MAX_DESC_CHARS


def test_prep_jd_cap_matches_score_budget() -> None:
    # Bound to the shared constant, not a 6000 literal. Fails at the old 6000.
    assert _JD_MAX_CHARS == MAX_DESC_CHARS == 16000


def test_answer_jd_context_keeps_midsize_jd_untruncated(
    tmp_path: Path, migrations_dir: Path
) -> None:
    # A 10k-char JD is the median-to-p90 real case: clipped under the old 6000
    # cap, fully retained under 16000.
    desc = "Requirements:\n" + ("Strong TypeScript and React. " * 350)
    assert 6000 < len(desc) < MAX_DESC_CHARS

    db_path = tmp_path / "jobhunt.db"
    conn = connect(db_path)
    migrate(conn, migrations_dir)
    upsert_job(
        conn,
        Job(
            id="greenhouse:acme:1",
            source="greenhouse",
            external_id="1",
            company="Acme",
            title="Full-Stack Developer",
            location="Toronto, ON",
            description=desc,
            url="https://example.com/1",
        ),
    )
    conn.commit()
    conn.close()

    cfg = Config(paths=PathsConfig(db_path=db_path, kb_dir=tmp_path / "kb"))
    jd_context, _ = _load_jd_context(cfg, "greenhouse:acme:1")

    assert "[truncated]" not in jd_context
    assert desc in jd_context  # full JD retained, not clipped at 6000
