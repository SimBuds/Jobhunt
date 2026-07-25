from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.commands import apply_cmd
from jobhunt.commands.list_cmd import _query
from jobhunt.db import connect, migrate, upsert_application, upsert_job, write_score
from jobhunt.models import Job


@pytest.fixture
def conn(tmp_path: Path, migrations_dir: Path):
    c = connect(tmp_path / "test.db")
    migrate(c, migrations_dir)
    yield c
    c.close()


def _job(suffix: str) -> Job:
    return Job(
        id=f"greenhouse:acme:{suffix}",
        source="greenhouse",
        external_id=suffix,
        company="acme",
        title=f"Dev {suffix}",
        location="Toronto, ON",
        description="Need Python.",
        url=f"https://example.com/{suffix}",
    )


def _seed(conn, suffix: str, *, score: int, status: str | None = None) -> None:
    job_id = f"greenhouse:acme:{suffix}"
    upsert_job(conn, _job(suffix))
    write_score(
        conn,
        job_id=job_id,
        score=score,
        reasons=[],
        red_flags=[],
        must_clarify=[],
        model="test",
        prompt_hash="test",
    )
    if status is not None:
        upsert_application(
            conn,
            application_id=f"app-{suffix}",
            job_id=job_id,
            status=status,
            resume_path=None,
            cover_path=None,
            fill_plan_path=None,
            applied_week="2026-W26",
        )


def test_apply_top_includes_drafted_but_excludes_submitted_states(conn) -> None:
    _seed(conn, "plain", score=80)
    _seed(conn, "drafted", score=95, status="drafted")
    _seed(conn, "applied", score=99, status="applied")
    _seed(conn, "withdrawn", score=98, status="withdrawn")

    rows = apply_cmd._resolve_top_n(conn, n=10, min_score=70)

    assert [r["id"] for r in rows] == [
        "greenhouse:acme:drafted",
        "greenhouse:acme:plain",
    ]


def test_list_default_targets_include_drafted_but_exclude_submitted_states(conn) -> None:
    _seed(conn, "plain", score=80)
    _seed(conn, "drafted", score=95, status="drafted")
    _seed(conn, "applied", score=99, status="applied")
    _seed(conn, "withdrawn", score=98, status="withdrawn")

    rows = _query(
        conn,
        week_label=None,
        status=None,
        min_score=None,
        source=None,
        no_reply=False,
        applied_before=None,
        limit=10,
        default_apply_targets=True,
    )

    assert [r["id"] for r in rows] == [
        "greenhouse:acme:drafted",
        "greenhouse:acme:plain",
    ]


# --- Phase A4: company/title fragments accepted where a job id is expected ---


def test_resolve_by_id_accepts_company_fragment(conn) -> None:
    """`jobhunt apply acme` resolves like `jobhunt track response acme` does."""
    _seed(conn, "77", score=80)
    rows = apply_cmd._resolve_by_id(conn, "dev 77")
    assert len(rows) == 1
    assert rows[0]["id"] == "greenhouse:acme:77"


def test_resolve_by_id_still_takes_exact_id(conn) -> None:
    _seed(conn, "78", score=80)
    rows = apply_cmd._resolve_by_id(conn, "greenhouse:acme:78")
    assert rows[0]["id"] == "greenhouse:acme:78"


def test_resolve_by_id_ambiguous_fragment_exits(conn, capsys) -> None:
    import typer

    _seed(conn, "79", score=80)
    _seed(conn, "80", score=80)
    with pytest.raises(typer.Exit) as e:
        apply_cmd._resolve_by_id(conn, "acme")
    assert e.value.exit_code == 1
    assert "ambiguous" in capsys.readouterr().err


def test_resolve_by_id_exact_id_wins_for_declined_job(conn) -> None:
    """The fragment path skips declined jobs; an explicit id must not."""
    _seed(conn, "81", score=80)
    with conn:
        conn.execute(
            "UPDATE jobs SET decline_reason = 'wrong_domain' WHERE id = ?",
            ("greenhouse:acme:81",),
        )
    rows = apply_cmd._resolve_by_id(conn, "greenhouse:acme:81")
    assert rows[0]["id"] == "greenhouse:acme:81"
