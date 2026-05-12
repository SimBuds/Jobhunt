from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunt.errors import PipelineError
from jobhunt.resume.parse_docx import (
    _split_skills,
    parse_baseline,
    write_kb_markdown,
    write_verified_json,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "Resume.docx"
LEGACY_BASELINE = REPO_ROOT / "Casey_Hsu_Resume_Baseline.docx"
if not BASELINE.is_file() and LEGACY_BASELINE.is_file():
    BASELINE = LEGACY_BASELINE


@pytest.mark.skipif(not BASELINE.is_file(), reason="baseline .docx not present")
def test_parse_baseline_round_trip(tmp_path: Path):
    facts = parse_baseline(BASELINE)
    assert facts.name
    assert "Toronto" in facts.contact_line
    assert len(facts.work_history) == 4

    employers = {r.employer for r in facts.work_history}
    assert "Custom Jewelry Brand (Atelier Dacko)" in employers
    assert "Sous Chef & Team Lead" in {r.title for r in facts.work_history}

    # Familiar must stay separate.
    assert "Python" in facts.skills_familiar
    assert "Python" not in facts.skills_core

    # Round-trip via verified.json.
    out = tmp_path / "verified.json"
    write_verified_json(facts, out)
    payload = json.loads(out.read_text())
    assert payload["name"] == facts.name
    assert len(payload["work_history"]) == 4

    # KB markdown writer leaves four files.
    kb = tmp_path / "kb"
    paths = write_kb_markdown(facts, kb)
    assert len(paths) == 4
    assert (kb / "profile" / "skills.md").read_text().count("## Familiar") == 1


def test_parse_baseline_missing_file_errors(tmp_path: Path):
    with pytest.raises(PipelineError, match="not found"):
        parse_baseline(tmp_path / "nope.docx")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("a, b, c", ["a", "b", "c"]),
        (
            "Shopify (Liquid, Custom Themes), HubSpot",
            ["Shopify (Liquid, Custom Themes)", "HubSpot"],
        ),
        ("foo (a, b, c), bar", ["foo (a, b, c)", "bar"]),
        ("  a  ,  b  ", ["a", "b"]),
        ("", []),
    ],
)
def test_split_skills_paren_aware(value: str, expected: list[str]):
    assert _split_skills(value) == expected
