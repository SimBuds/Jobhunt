from __future__ import annotations

from pathlib import Path

from jobhunt.browser.profile_map import build_field_map
from jobhunt.config import ApplicantProfile


def test_build_field_map_exposes_work_preferences() -> None:
    profile = ApplicantProfile(
        work_arrangements=["onsite", "hybrid", "remote"],
        employment_types=["full_time", "contract"],
    )
    fields = build_field_map(
        profile, resume_path=Path("/tmp/r.docx"), cover_path=Path("/tmp/c.docx")
    )
    assert fields["work_arrangement"] == "On-site, Hybrid, Remote"
    assert fields["employment_type"] == "Full-time, Contract"


def test_build_field_map_handles_single_preferences() -> None:
    profile = ApplicantProfile(
        work_arrangements=["remote"],
        employment_types=["contract"],
    )
    fields = build_field_map(
        profile, resume_path=Path("/tmp/r.docx"), cover_path=Path("/tmp/c.docx")
    )
    assert fields["work_arrangement"] == "Remote"
    assert fields["employment_type"] == "Contract"
