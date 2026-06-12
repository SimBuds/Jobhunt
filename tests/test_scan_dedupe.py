"""Cross-source dedupe at the scan drain chokepoint (Phase B2).

Exercises `_dedup_decision` + `upsert_job` the same way `_ingest_all`'s drain
loop composes them: a direct-ATS row and an aggregator row for the same
posting yield one surviving DB row in either arrival order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.commands.scan_cmd import _dedup_decision, _dedup_key
from jobhunt.db import connect, migrate, upsert_job
from jobhunt.models import Job


@pytest.fixture
def conn(tmp_path: Path, migrations_dir: Path):
    c = connect(tmp_path / "test.db")
    migrate(c, migrations_dir)
    yield c
    c.close()


def _gh_job() -> Job:
    return Job(
        id="greenhouse:acme:42",
        source="greenhouse",
        external_id="42",
        company="ACME Inc",
        title="Full-Stack Developer",
        location="Toronto, ON",
        description="A long, rich JD straight from the ATS." * 10,
        url="https://boards.greenhouse.io/acme/jobs/42",
    )


def _adzuna_job() -> Job:
    return Job(
        id="adzuna_ca:9001",
        source="adzuna_ca",
        external_id="9001",
        company="ACME Inc",
        title="Full-Stack Developer",
        location="Toronto, ON",
        description="Short snippet…",
        url="https://www.adzuna.ca/land/ad/9001",
    )


def _drain(conn, jobs: list[Job]) -> int:
    """Mirror of the drain loop's dedupe + upsert composition."""
    inserted = 0
    seen: set[str] = set()
    agg_shadow: dict[str, str] = {}
    for item in jobs:
        skip, stale_agg_id, shadow_claim = _dedup_decision(item, seen, agg_shadow)
        if skip:
            continue
        with conn:
            if stale_agg_id is not None:
                conn.execute("DELETE FROM jobs WHERE id = ?", (stale_agg_id,))
                inserted -= 1
            if upsert_job(conn, item):
                inserted += 1
                if shadow_claim is not None:
                    agg_shadow[shadow_claim] = item.id
    return inserted


def test_direct_first_aggregator_dropped(conn) -> None:
    inserted = _drain(conn, [_gh_job(), _adzuna_job()])
    rows = [r[0] for r in conn.execute("SELECT id FROM jobs")]
    assert rows == ["greenhouse:acme:42"]
    assert inserted == 1


def test_aggregator_first_direct_supersedes(conn) -> None:
    inserted = _drain(conn, [_adzuna_job(), _gh_job()])
    rows = [r[0] for r in conn.execute("SELECT id FROM jobs")]
    assert rows == ["greenhouse:acme:42"]
    assert inserted == 1


def test_two_aggregator_copies_collapse(conn) -> None:
    second = _adzuna_job().model_copy(update={"id": "adzuna_ca:9002", "external_id": "9002"})
    inserted = _drain(conn, [_adzuna_job(), second])
    assert inserted == 1
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_distinct_postings_both_kept(conn) -> None:
    other = _adzuna_job().model_copy(
        update={"id": "adzuna_ca:7", "external_id": "7", "title": "Shopify Developer"}
    )
    inserted = _drain(conn, [_gh_job(), other])
    assert inserted == 2


def test_same_board_double_post_skipped(conn) -> None:
    # Boards double-post the same role under two posting ids (observed:
    # Speechify, byte-identical Greenhouse JDs 13 minutes apart). The second
    # direct copy must skip on the shadow claimed by the first.
    second = _gh_job().model_copy(
        update={"id": "greenhouse:acme:43", "external_id": "43"}
    )
    inserted = _drain(conn, [_gh_job(), second])
    rows = [r[0] for r in conn.execute("SELECT id FROM jobs")]
    assert rows == ["greenhouse:acme:42"]
    assert inserted == 1


def test_cross_board_direct_dup_skipped(conn) -> None:
    # Same company, same title on two different direct ATS boards — score once.
    ashby_copy = _gh_job().model_copy(
        update={
            "id": "ashby:acme:abc",
            "source": "ashby",
            "external_id": "abc",
            "url": "https://jobs.ashbyhq.com/acme/abc",
        }
    )
    inserted = _drain(conn, [_gh_job(), ashby_copy])
    rows = [r[0] for r in conn.execute("SELECT id FROM jobs")]
    assert rows == ["greenhouse:acme:42"]
    assert inserted == 1


def test_aggregator_then_two_directs_one_survives(conn) -> None:
    # Aggregator copy is superseded by the first direct row; the second
    # direct copy then skips on the direct-claimed shadow.
    second = _gh_job().model_copy(
        update={"id": "greenhouse:acme:43", "external_id": "43"}
    )
    inserted = _drain(conn, [_adzuna_job(), _gh_job(), second])
    rows = [r[0] for r in conn.execute("SELECT id FROM jobs")]
    assert rows == ["greenhouse:acme:42"]
    assert inserted == 1


def test_preexisting_aggregator_row_not_deleted(conn) -> None:
    # Cross-scan case (B3, deferred): the aggregator row landed in an earlier
    # scan, so this scan's agg_shadow never claims it and the direct row must
    # NOT delete it.
    with conn:
        assert upsert_job(conn, _adzuna_job()) is True
    inserted = _drain(conn, [_gh_job()])
    assert inserted == 1
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_dedup_key_shapes() -> None:
    assert len(_dedup_key(_gh_job())) == 2
    assert _dedup_key(_adzuna_job()) == (_dedup_key(_gh_job())[1],)
