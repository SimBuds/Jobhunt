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
    # Healthcare research/coordination layer (UHN leak, 2026-06-11 scan).
    "Patient Flow Coordinator",
    "Patient Care Coordinator",
    "Clinical Research Coordinator III (PK)",
    "Clinical Research Coordinator III (Leukemia/BMT)",
    "Clinical Research Student (Nursing)",
    "Research Technician I",
    "Postdoctoral Researcher",
    "Supervisor, Interventional Radiology",
    "Counsellor to Students",
    "Staffing Representative",
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
    "Clinical Research Platform Engineer",
    "Software Developer, Clinical Research Systems",
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


# Phase D11b (2026-11-11): titles that leaked past the filter on a live scan
# and were each declined as non-engineering after a full scoring call.
LEAKED_2026_11 = [
    "Corporate Legal Assistant",
    "Customer Care Representative",
    "Payouts Administrator",
    "Jr. Payment Administrator (Full-Time)",
    "Mortgage Broker (Independent Contractor)",
    "Senior Specialist, Benefits",
    "Medical Writer (RID00242)",
    "Arrivals Vehicle Inspector",
    "Car Detailer",
    "Condition Report Writer",
    "Licensed Automotive Technician – Evening Shift (Hourly Pay)",
    "Shuttler Driver (6-Month Fixed-Term)",
    "Vehicle Quality-Control Associate (Evening Shift)",
    "Production Cook",
    "Senior Financial Analyst",
    "Estimator",
    "Actuarial Analyst",
    "Claims Advisor Commercial & Personal Auto",
]


@pytest.mark.parametrize("title", LEAKED_2026_11)
def test_leaked_titles_dropped(title: str) -> None:
    assert is_non_engineering_title(title) is True


@pytest.mark.parametrize(
    "title",
    [
        # A technical role whose title names a benefits domain stays.
        "Solutions Architect - Retirement & Benefits Solutions",
        "Software Developer, Payments Administrator Portal",
        "Web Developer, Automotive Technician Scheduling",
    ],
)
def test_new_terms_do_not_drop_technical_roles(title: str) -> None:
    assert is_non_engineering_title(title) is False
