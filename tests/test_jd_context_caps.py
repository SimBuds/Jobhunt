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
    # The contract is that prep tracks the scoring budget, whatever that budget
    # currently is — asserting a literal only pinned one revision of it. The
    # value moves with num_ctx (16000 at num_ctx=32768, 10000 at 16384), so pin
    # the binding plus the floor this initiative existed to clear.
    assert _JD_MAX_CHARS == MAX_DESC_CHARS
    assert MAX_DESC_CHARS > 6000  # the old cap that truncated 63% of real JDs


def test_answer_jd_context_keeps_midsize_jd_untruncated(
    tmp_path: Path, migrations_dir: Path
) -> None:
    # A mid-size JD comfortably above the old 6000 cap: clipped then, fully
    # retained now. Sized as a fraction of the live budget so this keeps
    # testing retention rather than silently becoming a truncation test the
    # next time the budget moves.
    filler = "Strong TypeScript and React. "
    desc = "Requirements:\n" + filler * (((MAX_DESC_CHARS * 3 // 4) - 14) // len(filler))
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
