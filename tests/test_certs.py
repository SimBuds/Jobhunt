from __future__ import annotations

import pytest

from jobhunt.analyze.certs import extract_certs, tally


# ---------------------------------------------------------------------------
# extract_certs — known certs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        # AWS variants
        ("AWS Certified Solutions Architect – Associate preferred.", ["AWS Certified Solutions Architect – Associate"]),
        ("AWS Certified Solutions Architect - Professional a plus.", ["AWS Certified Solutions Architect – Professional"]),
        ("AWS Certified Developer experience required.", ["AWS Certified Developer"]),
        ("AWS Certified DevOps Engineer certification.", ["AWS Certified DevOps Engineer"]),
        ("AWS Cloud Practitioner or higher.", ["AWS Cloud Practitioner"]),
        # GCP
        ("GCP ACE or equivalent.", ["GCP Associate Cloud Engineer"]),
        ("Google Professional Cloud Architect preferred.", ["GCP Professional Cloud Architect"]),
        # Azure
        ("AZ-104 required.", ["Azure AZ-104"]),
        ("AZ900 is a nice to have.", ["Azure AZ-900"]),
        ("AZ-204 and AZ-305 preferred.", ["Azure AZ-204", "Azure AZ-305"]),
        # Security
        ("Candidates must hold CISSP.", ["CISSP"]),
        ("Security+ or equivalent.", ["Security+"]),
        ("Sec+ certification preferred.", ["Security+"]),
        ("CompTIA Security+ required.", ["Security+"]),
        ("CEH certification.", ["CEH"]),
        ("OSCP is a plus.", ["OSCP"]),
        # PM / Agile
        ("PMP required.", ["PMP"]),
        ("PRINCE2 or PMP preferred.", ["PRINCE2", "PMP"]),
        ("CSM or PSM certification.", ["CSM", "PSM"]),
        ("ITIL v4 knowledge.", ["ITIL"]),
        ("SAFe practitioner.", ["SAFe"]),
        # Networking
        ("CCNA or CCNP required.", ["CCNA", "CCNP"]),
        ("RHCSA or RHCE Linux cert.", ["RHCSA", "RHCE"]),
        ("CompTIA Network+ a plus.", ["CompTIA Network+"]),
        # Finance
        ("CFA Level II candidate.", ["CFA"]),
        ("CPA designation required.", ["CPA"]),
        # Case-insensitive
        ("cissp and pmp are required.", ["CISSP", "PMP"]),
        ("aws certified solutions architect – associate", ["AWS Certified Solutions Architect – Associate"]),
    ],
)
def test_known_certs(text: str, expected: list[str]) -> None:
    assert extract_certs(text) == expected


# ---------------------------------------------------------------------------
# extract_certs — word-boundary safety (no false positives)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "PMPro is a WordPress plugin.",          # PMPro must not match PMP
        "ASEC+ protocol.",                       # ASEC+ must not match Sec+
        "We use CCNAS library.",                 # CCNAS must not match CCNA
        "The CCNPX router.",                     # CCNPX must not match CCNP
        "Visit cfasite.com for details.",        # cfasite must not match CFA
    ],
)
def test_no_false_positives(text: str) -> None:
    assert extract_certs(text) == []


# ---------------------------------------------------------------------------
# extract_certs — generic patterns
# ---------------------------------------------------------------------------


def test_generic_certified_kubernetes() -> None:
    result = extract_certs("Certified Kubernetes Administrator (CKA) preferred.")
    assert "Kubernetes Administrator" in result


def test_generic_certification_suffix() -> None:
    result = extract_certs("Docker Swarm Certification is a bonus.")
    assert "Docker Swarm" in result


def test_known_cert_not_double_counted_by_generic() -> None:
    # "AWS Certified Solutions Architect" contains "Certified", but the known
    # pattern should consume it; generic should not produce a second entry.
    result = extract_certs("AWS Certified Solutions Architect – Associate required.")
    known_hits = [c for c in result if "AWS" in c]
    assert len(known_hits) == 1


# ---------------------------------------------------------------------------
# tally
# ---------------------------------------------------------------------------


def test_tally_aggregates_across_rows() -> None:
    rows = [
        {"title": "Backend Engineer", "description": "PMP required. AWS Certified Developer a plus."},
        {"title": "DevOps Engineer", "description": "AWS Certified Developer preferred. CISSP nice to have."},
        {"title": "Data Engineer", "description": "No certs required."},
    ]
    counts = tally(rows)
    assert counts["AWS Certified Developer"] == 2
    assert counts["PMP"] == 1
    assert counts["CISSP"] == 1


def test_tally_cert_counted_once_per_job() -> None:
    rows = [
        {
            "title": "Security Engineer",
            "description": "CISSP required. CISSP or equivalent. Must have CISSP.",
        }
    ]
    counts = tally(rows)
    assert counts["CISSP"] == 1


def test_tally_empty_rows() -> None:
    assert tally([]) == {}


def test_tally_none_description() -> None:
    rows = [{"title": "Engineer", "description": None}]
    counts = tally(rows)
    assert len(counts) == 0
