"""Phase 8 tests — seed_mars.py candidate list shape.

No live HTTP. Validates the script's hand-curated candidate list structure
and exercises the probe loop with a stub so the emit-TOML path is exercised.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "seed_mars.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("seed_mars", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_candidates_only_uses_probeable_atses() -> None:
    mod = _load_script()
    keys = set(mod.CANDIDATES.keys())
    # Probeable ATSes only — Workday is excluded by design (uses tenant
    # triple, not raw slug; documented in the script header).
    assert keys <= {"greenhouse", "lever", "ashby", "smartrecruiters"}
    assert "workday" not in keys


def test_candidates_are_lowercase_simple_strings() -> None:
    """Greenhouse / Lever / Ashby slugs are lowercased single tokens.
    SmartRecruiters is the exception — case-sensitive, can include caps.
    """
    mod = _load_script()
    for ats in ("greenhouse", "lever", "ashby"):
        for slug in mod.CANDIDATES.get(ats, []):
            assert slug == slug.lower(), f"{ats}/{slug} should be lowercase"
            assert " " not in slug, f"{ats}/{slug} has whitespace"


def test_no_duplicate_candidates_within_ats() -> None:
    mod = _load_script()
    for ats, slugs in mod.CANDIDATES.items():
        assert len(slugs) == len(set(slugs)), f"duplicate candidate(s) under {ats}"


def test_candidates_skew_to_ai_startup_space() -> None:
    """Sanity assertion — the script is *for* the MaRS / AI angle. Ashby
    is the AI-startup ATS in 2026, so the ashby bucket should be the
    largest list. Keeps the script's purpose explicit and prevents
    accidental rewrites from converting it into a generic seed."""
    mod = _load_script()
    ashby_count = len(mod.CANDIDATES.get("ashby", []))
    gh_count = len(mod.CANDIDATES.get("greenhouse", []))
    assert ashby_count >= 15, (
        f"ashby has only {ashby_count} candidates — the AI/LLM angle is the "
        "point of this script; flesh out the list"
    )
    # Loose ordering — but greenhouse and ashby are both meaningful here.
    assert gh_count >= 10, f"greenhouse has only {gh_count} candidates"


@pytest.mark.asyncio
async def test_main_emits_toml_with_only_live_hits(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end stub: run main() with `_probe_one` returning a deterministic
    mix of 200s and 404s. Assert the TOML block emitted at the end contains
    only the live slugs.
    """
    mod = _load_script()

    from jobhunt.discover.probe import ProbeOutcome

    # Shrink the candidate list so the test stays fast and deterministic.
    monkeypatch.setattr(
        mod,
        "CANDIDATES",
        {
            "greenhouse": ["live-co", "stale-co"],
            "ashby": ["live-ash"],
            "lever": [],
            "smartrecruiters": [],
        },
    )

    async def fake_probe(
        client: Any, limiter: Any, company: str, ats: str, slug: str
    ) -> ProbeOutcome:
        if slug == "stale-co":
            return ProbeOutcome(company, ats, slug, 404, None)
        return ProbeOutcome(company, ats, slug, 200, 7)

    monkeypatch.setattr(mod, "_probe_one", fake_probe)

    await mod.main()
    out = capsys.readouterr().out
    # Verified TOML output should include live slugs only.
    assert 'greenhouse = ["live-co"]' in out
    assert 'ashby = ["live-ash"]' in out
    assert "stale-co" not in out.split("verified MaRS")[1]
