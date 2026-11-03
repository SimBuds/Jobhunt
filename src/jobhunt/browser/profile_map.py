"""Map applicant profile + tailored docs to a flat key→value dict for autofill."""

from __future__ import annotations

from pathlib import Path

from jobhunt.config import ApplicantProfile

_ARRANGEMENT_LABELS = {"onsite": "On-site", "hybrid": "Hybrid", "remote": "Remote"}
_EMPLOYMENT_LABELS = {
    "full_time": "Full-time",
    "part_time": "Part-time",
    "contract": "Contract",
    "internship": "Internship",
    "temporary": "Temporary",
}


def _pretty_arrangement(value: str) -> str:
    return _ARRANGEMENT_LABELS.get(value, value)


def _pretty_employment(value: str) -> str:
    return _EMPLOYMENT_LABELS.get(value, value)


def _yes_no(value: bool | None) -> str:
    # "" for unset: every handler skips an empty value, so an unanswered
    # question stays blank instead of being answered "No" on the user's behalf.
    return "" if value is None else ("Yes" if value else "No")


def build_field_map(
    profile: ApplicantProfile,
    *,
    resume_path: Path,
    cover_path: Path,
) -> dict[str, str]:
    """Common keys handlers can look up. Values are strings (paths for uploads)."""
    first, _, last = profile.full_name.partition(" ")
    return {
        "full_name": profile.full_name,
        "first_name": first,
        "last_name": last or first,
        "email": profile.email,
        "phone": profile.phone,
        "linkedin": profile.linkedin_url,
        "github": profile.github_url,
        "portfolio": profile.portfolio_url,
        "website": profile.portfolio_url,
        "city": profile.city,
        "region": profile.region,
        "country": profile.country,
        "work_auth_canada": _yes_no(profile.work_auth_canada),
        "requires_visa_sponsorship": _yes_no(profile.requires_visa_sponsorship),
        "salary_expectation": profile.salary_expectation_cad,
        "pronouns": profile.pronouns,
        "work_arrangement": ", ".join(_pretty_arrangement(a) for a in profile.work_arrangements),
        "employment_type": ", ".join(_pretty_employment(t) for t in profile.employment_types),
        "resume_path": str(resume_path),
        "cover_letter_path": str(cover_path),
    }
