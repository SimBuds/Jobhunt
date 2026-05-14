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


# ---------------------------------------------------------------------------
# extract_certs_split + tally_split
# ---------------------------------------------------------------------------


def test_extract_certs_split_separates_known_and_generic() -> None:
    from jobhunt.analyze.certs import extract_certs_split
    text = (
        "CISSP required. Azure Security Engineer Certification a plus. "
        "Certified Banana Specialist preferred."
    )
    known, generic = extract_certs_split(text)
    assert "CISSP" in known
    # Generic patterns catch unknown multi-word certs.
    assert any("Banana Specialist" in g for g in generic)
    # No known cert leaks into the generic list.
    assert "CISSP" not in generic


def test_extract_certs_split_drops_generic_collision_with_known() -> None:
    """If a generic phrase happens to equal a known cert name, only the known
    side keeps it — the generic list must not duplicate."""
    from jobhunt.analyze.certs import extract_certs_split
    text = "CISSP required."
    known, generic = extract_certs_split(text)
    assert known == ["CISSP"]
    assert "CISSP" not in generic


def test_tally_split_counts_per_job() -> None:
    from jobhunt.analyze.certs import tally_split
    rows = [
        {"title": "A", "description": "CISSP required. Certified Banana Specialist preferred."},
        {"title": "B", "description": "Certified Banana Specialist preferred."},
        {"title": "C", "description": "PMP only."},
    ]
    known, generic = tally_split(rows)
    assert known["CISSP"] == 1
    assert known["PMP"] == 1
    assert generic["Banana Specialist"] == 2


# ---------------------------------------------------------------------------
# Trend classification
# ---------------------------------------------------------------------------


def test_classify_emerging_threshold() -> None:
    from jobhunt.commands.analyze_cmd import _classify
    # cur >= 3 with prev=0 → emerging.
    _, label = _classify(0, 5)
    assert "emerging" in label
    # cur < 3 with prev=0 → low signal.
    _, label = _classify(0, 2)
    assert "low signal" in label


def test_classify_rising_falling_stable() -> None:
    from jobhunt.commands.analyze_cmd import _classify
    pct, label = _classify(10, 20)  # +100%
    assert label == "📈 rising"
    pct, label = _classify(20, 5)   # -75%
    assert label == "📉 falling"
    pct, label = _classify(10, 12)  # +20%, stable
    assert label == "stable"


def test_classify_dropped() -> None:
    from jobhunt.commands.analyze_cmd import _classify
    pct, label = _classify(10, 0)
    assert pct == -100.0
    assert label == "dropped"


# ---------------------------------------------------------------------------
# Verdict rubric (cert decision tool)
# ---------------------------------------------------------------------------


def test_classify_verdict_strong_emerging() -> None:
    from jobhunt.commands.analyze_cmd import _classify_verdict
    v = _classify_verdict(fit_cur=8, cur=8, trend_label="🚀 emerging", demand_rank=5)
    assert v == "Strong emerging signal"


def test_classify_verdict_worth_pursuing() -> None:
    from jobhunt.commands.analyze_cmd import _classify_verdict
    v = _classify_verdict(fit_cur=14, cur=28, trend_label="📈 rising", demand_rank=2)
    assert v == "Worth pursuing"


def test_classify_verdict_wrong_direction() -> None:
    from jobhunt.commands.analyze_cmd import _classify_verdict
    # High market demand, zero fit → not for you.
    v = _classify_verdict(fit_cur=0, cur=25, trend_label="stable", demand_rank=1)
    assert v == "Wrong direction"


def test_classify_verdict_skip_low_fit() -> None:
    from jobhunt.commands.analyze_cmd import _classify_verdict
    # fit < 3 always skips, even if rising in the market.
    v = _classify_verdict(fit_cur=2, cur=8, trend_label="🚀 emerging", demand_rank=4)
    assert v == "Skip"


def test_classify_verdict_late_diminishing() -> None:
    from jobhunt.commands.analyze_cmd import _classify_verdict
    v = _classify_verdict(fit_cur=5, cur=10, trend_label="📉 falling", demand_rank=8)
    assert v == "Late — diminishing"


def test_classify_verdict_stable_staple() -> None:
    from jobhunt.commands.analyze_cmd import _classify_verdict
    v = _classify_verdict(fit_cur=15, cur=40, trend_label="stable", demand_rank=3)
    assert v == "Stable staple"
    # Stable but outside top 10 demand → Marginal.
    v = _classify_verdict(fit_cur=4, cur=4, trend_label="stable", demand_rank=15)
    assert v == "Marginal"


def test_classify_verdict_wrong_direction_threshold() -> None:
    """`fit_cur=0` only flags 'Wrong direction' when cur >= 5; otherwise Skip."""
    from jobhunt.commands.analyze_cmd import _classify_verdict
    v = _classify_verdict(fit_cur=0, cur=4, trend_label="stable", demand_rank=20)
    assert v == "Skip"


def test_analyze_certs_min_score_snapshot_runs(tmp_path) -> None:
    """Integration: --min-score path round-trips through the snapshot render."""
    import sqlite3
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE jobs (id TEXT PRIMARY KEY, source TEXT, external_id TEXT,
          company TEXT, title TEXT, location TEXT, remote_type TEXT,
          description TEXT, url TEXT, posted_at TIMESTAMP,
          ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, raw_json TEXT,
          decline_reason TEXT);
        CREATE TABLE scores (job_id TEXT PRIMARY KEY REFERENCES jobs(id),
          score INTEGER NOT NULL, reasons TEXT, red_flags TEXT,
          must_clarify TEXT, model TEXT, prompt_hash TEXT,
          scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    """)
    # 3 fit jobs (score=80) all mention CISSP, 3 non-fit (score=40) mention CISSP.
    for i in range(3):
        con.execute(
            "INSERT INTO jobs(id, source, external_id, title, description) VALUES (?,?,?,?,?)",
            (f"fit:{i}", "t", str(i), f"Sec Eng {i}", "CISSP required."),
        )
        con.execute("INSERT INTO scores(job_id, score) VALUES (?, ?)", (f"fit:{i}", 80))
    for i in range(3):
        con.execute(
            "INSERT INTO jobs(id, source, external_id, title, description) VALUES (?,?,?,?,?)",
            (f"low:{i}", "t", str(i + 100), f"Other {i}", "CISSP required."),
        )
        con.execute("INSERT INTO scores(job_id, score) VALUES (?, ?)", (f"low:{i}", 40))
    con.commit()
    # Drive _render_snapshot directly with min_score=65 — should see 3 fit jobs.
    from jobhunt.commands.analyze_cmd import _render_snapshot
    import typer
    try:
        _render_snapshot(con, top=10, min_score=65)
    except typer.Exit:
        pass  # snapshot calls Exit(0) when done — that's fine
    con.close()
