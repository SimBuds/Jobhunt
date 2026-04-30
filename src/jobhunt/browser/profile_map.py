"""Map applicant profile + tailored docs to a flat key→value dict for autofill."""

from __future__ import annotations

from pathlib import Path

from jobhunt.config import ApplicantProfile


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
        "work_auth_canada": "Yes" if profile.work_auth_canada else "No",
        "requires_visa_sponsorship": "Yes" if profile.requires_visa_sponsorship else "No",
        "salary_expectation": profile.salary_expectation_cad,
        "pronouns": profile.pronouns,
        "resume_path": str(resume_path),
        "cover_letter_path": str(cover_path),
    }
