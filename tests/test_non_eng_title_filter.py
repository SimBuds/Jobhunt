"""Unit tests for the non-engineering-title ingest filter (pure, no network)."""

from __future__ import annotations

import pytest

from jobhunt.ingest._filter import is_non_engineering_title

# Real decline-data titles that should be dropped before scoring.
NON_ENG_TITLES = [
    "Account Executive - Account Management & New",
    "Enterprise Account Executive (Auth0)",
    "[CA DC FACTOR] Office Administrator",
    "Food Safety Specialist",
    "FSQA Technician",
    "Legal Counsel (16 Month Contract)",
    "Product Counsel, Terminal",
    "Maintenance Technician I",
    "Maintenance Technician II",
    "Operational Buyer (6 months contract)",
    "Supply Planner",
    "Production Supervisor",
    "Sanitation Associate",
    "Communication Specialist-Internal Communications",
    "Associate, Performance Marketing",
    "Senior Recruiter",
    "Warehouse Associate",
    "Delivery Driver",
    # Healthcare-clinical roles (hospital tenants like UHN post these heavily).
    "Personal Support Worker - Spinal Cord Rehab Program",
    "Physiotherapist, Inpatient",
    "Respiratory Therapist",
    "Discipline Head, Respiratory Therapy",
    "Radiation Therapist",
    "Occupational Therapist",
    "Social Worker",
    "Registered Dietitian",
    "Speech Language Pathologist - Toronto Rehab",
    "Professor, Cardiovascular Perfusion",
    "Medical Laboratory Technologist, Genetics",
    "Technologist Assistant II, Computed Tomography (CT)",
    "Client Care Attendant",
    "SAI Ward Clerk - Temp Full time",
]

# Engineering / dev titles that MUST be kept (guard wins).
ENG_TITLES = [
    "Software Developer",
    "Full Stack Developer",
    "Full-Stack (React / Node) Developer",
    "Frontend Developer (React, TypeScript, WebGL)",
    "Backend Engineer, Consumer",
    "Engineer I - DevOps",
    "Machine Learning Ops Developer",
    "Associate Software Technical Analyst",
    "Web Developer",
    "Senior Software Developer",
    "Associate QA - Contractor",
    "AI Systems Engineer",
    # Eng guard must win even when a clinical/healthcare token co-occurs.
    "Healthcare Software Engineer",
    "Clinical Application Developer",
    # Real UHN data/eng roles that must survive (no clinical token, or guarded).
    "Bioinformatics Analyst",
    "Machine Learning Specialist",
    "Technical Specialist, Cybersecurity",
]


@pytest.mark.parametrize("title", NON_ENG_TITLES)
def test_non_eng_titles_dropped(title: str) -> None:
    assert is_non_engineering_title(title) is True


@pytest.mark.parametrize("title", ENG_TITLES)
def test_eng_titles_kept(title: str) -> None:
    assert is_non_engineering_title(title) is False


def test_eng_guard_wins_over_coincidental_non_eng_token() -> None:
    # "logistics" is a non-eng token, but the dev signal must win.
    assert is_non_engineering_title("Software Developer - Logistics Platform") is False


def test_none_and_empty() -> None:
    assert is_non_engineering_title(None) is False
    assert is_non_engineering_title("") is False
