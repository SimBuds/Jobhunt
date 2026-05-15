from __future__ import annotations

import pytest

from jobhunt.discover.url_extract import ExtractedSlug, extract


@pytest.mark.parametrize(
    "url,expected",
    [
        # Greenhouse — both legacy and current hosts
        (
            "https://boards.greenhouse.io/braze/jobs/7564362?gh_jid=7564362",
            ExtractedSlug("greenhouse", "braze", None, None),
        ),
        (
            "https://job-boards.greenhouse.io/okta/jobs/123",
            ExtractedSlug("greenhouse", "okta", None, None),
        ),
        # Ashby
        (
            "https://jobs.ashbyhq.com/harvey/6ad3902b-2888-4c02-913d-de942e807133",
            ExtractedSlug("ashby", "harvey", None, None),
        ),
        # Lever
        (
            "https://jobs.lever.co/figma/some-posting-uuid/apply",
            ExtractedSlug("lever", "figma", None, None),
        ),
        # SmartRecruiters — both hosts
        (
            "https://jobs.smartrecruiters.com/Bosch/743999999999-engineer",
            ExtractedSlug("smartrecruiters", "Bosch", None, None),
        ),
        (
            "https://careers.smartrecruiters.com/Verizon",
            ExtractedSlug("smartrecruiters", "Verizon", None, None),
        ),
        # Workday — locale prefix stripped; site + wd-host captured
        (
            "https://rbc.wd3.myworkdayjobs.com/en-US/RBC_Careers/job/Toronto/Developer_R-12345",
            ExtractedSlug("workday", "rbc", "RBC_Careers", "wd3"),
        ),
        (
            "https://td.wd1.myworkdayjobs.com/TD_Bank_Careers",
            ExtractedSlug("workday", "td", "TD_Bank_Careers", "wd1"),
        ),
        # iCIMS
        (
            "https://careers-acme.icims.com/jobs/12345/some-role/job",
            ExtractedSlug("icims", "acme", None, None),
        ),
    ],
)
def test_extract_known_shapes(url: str, expected: ExtractedSlug) -> None:
    assert extract(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://www.adzuna.ca/details/5729068491?utm_medium=api",
        "https://example.com/jobs/123",
        "https://boards.greenhouse.io/",  # no slug segment
        "https://jobs.lever.co/",
        "not a url at all",
    ],
)
def test_extract_unknown_or_empty_returns_none(url: str) -> None:
    assert extract(url) is None
