"""Phase 4 tests — decline-reason classifier + backfill."""
from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.db import connect, migrate, set_decline_reason, upsert_job
from jobhunt.models import Job
from jobhunt.pipeline._decline_classify import (
    VALID_CATEGORIES,
    backfill_existing,
    classify_decline_reason,
)


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
        description="…",
        url=f"https://example.com/{suffix}",
    )


# --- classify_decline_reason — per category --------------------------------


@pytest.mark.parametrize("reason,expected", [
    ("Requires 7+ years of production Python.", "years_gap"),
    ("Insufficient years for this role.", "years_gap"),
    ("Hiring manager role with direct reports.", "people_management"),
    ("VP Engineering people-management track.", "people_management"),
    ("Must be located in San Francisco.", "location_mismatch"),
    ("US residents only, on-site in NYC.", "location_mismatch"),
    ("Role requires production Go and Rust experience.", "wrong_stack"),
    ("Primary stack is Ruby on Rails.", "wrong_stack"),
    ("Specific to fintech industry.", "wrong_domain"),
    ("Industry experience required.", "wrong_domain"),
    ("Clinical software for FDA-regulated devices.", "regulated_domain"),
    ("Securities trading platform; investment-bank background.", "regulated_domain"),
    ("Matched skills are all Familiar (academic/light use only).", "familiar_only"),
    ("Coursework-only Java.", "familiar_only"),
])
def test_classify_known_patterns(reason: str, expected: str) -> None:
    assert classify_decline_reason(reason) == expected


def test_classify_falls_through_to_other() -> None:
    assert classify_decline_reason("Some unrelated reason.") == "other"


def test_classify_none_or_empty_returns_none() -> None:
    assert classify_decline_reason(None) is None
    assert classify_decline_reason("") is None


def test_valid_categories_includes_other() -> None:
    assert "other" in VALID_CATEGORIES


# --- set_decline_reason live-stamps the category ----------------------------


def test_set_decline_reason_stamps_category(conn) -> None:
    upsert_job(conn, _job("a"))
    set_decline_reason(conn, "greenhouse:acme:a", "Requires 7+ years of production Python.")
    row = conn.execute(
        "SELECT decline_reason, decline_category FROM jobs WHERE id = ?",
        ("greenhouse:acme:a",),
    ).fetchone()
    assert row["decline_category"] == "years_gap"


def test_set_decline_reason_clearing_clears_category(conn) -> None:
    upsert_job(conn, _job("a"))
    set_decline_reason(conn, "greenhouse:acme:a", "Some reason")
    set_decline_reason(conn, "greenhouse:acme:a", None)
    row = conn.execute(
        "SELECT decline_reason, decline_category FROM jobs WHERE id = ?",
        ("greenhouse:acme:a",),
    ).fetchone()
    assert row["decline_reason"] is None
    assert row["decline_category"] is None


# --- backfill ---------------------------------------------------------------


def test_backfill_classifies_existing_rows(conn) -> None:
    """Insert jobs with decline_reason but force decline_category to NULL,
    then run backfill and assert categories populated."""
    for i, reason in enumerate([
        "Requires 5+ years",
        "Hiring manager role",
        "Some odd reason that won't match",
    ]):
        upsert_job(conn, _job(str(i)))
        conn.execute(
            "UPDATE jobs SET decline_reason = ?, decline_category = NULL WHERE id = ?",
            (reason, f"greenhouse:acme:{i}"),
        )
    conn.commit()

    updated = backfill_existing(conn)
    assert updated == 3
    rows = conn.execute(
        "SELECT id, decline_category FROM jobs WHERE decline_reason IS NOT NULL ORDER BY id"
    ).fetchall()
    cats = [r["decline_category"] for r in rows]
    assert cats == ["years_gap", "people_management", "other"]


def test_backfill_skips_already_classified(conn) -> None:
    upsert_job(conn, _job("a"))
    set_decline_reason(conn, "greenhouse:acme:a", "Requires 5+ years")
    # Already stamped by set_decline_reason. Backfill should be a no-op.
    updated = backfill_existing(conn)
    assert updated == 0
