"""Tests for pipeline.cover_validate — no LLM, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunt.pipeline.cover import CoverLetter
from jobhunt.pipeline.cover_validate import validate_cover

VERIFIED_PATH = Path(__file__).parent.parent / "kb" / "profile" / "verified.json"


@pytest.fixture
def verified() -> dict:
    if VERIFIED_PATH.is_file():
        return json.loads(VERIFIED_PATH.read_text())
    # Minimal stub if kb/ not present in CI.
    return {
        "work_history": [
            {
                "bullets": [
                    "Built and maintained a 14+ page Shopify storefront with 200+ product SKUs serving 500+ monthly visitors.",
                    "Cut page load time by 30%.",
                ]
            }
        ],
        "summary": "Full-stack developer with 2+ years experience.",
    }


def _good_cover(company: str = "Acme Corp") -> CoverLetter:
    return CoverLetter(
        salutation="Dear Hiring Team,",
        body=[
            f"I applied to {company} after reading the job description for the Full-Stack Developer role. The emphasis on TypeScript and Shopify maps cleanly onto my contract work over the past two years.",
            "The centrepiece project is the 14+ page Shopify storefront I built for a custom jewellery client. I migrated them from WordPress across three phases, wrote all the Liquid templates, and integrated Stripe payments — the store now serves 500+ monthly visitors with 200+ SKUs.",
            "A second relevant project: I built a custom HubSpot theme from scratch for an AI agency, cut page load time by 30%, and set up GitHub Actions CI before handing off to their team.",
            "I'd like to talk through how this work fits the role.",
        ],
        sign_off="Best,\nCasey Hsu",
        model="test",
    )


def test_clean_cover_no_violations(verified: dict) -> None:
    cover = _good_cover()
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert violations == [], violations


def test_banned_phrase_flagged(verified: dict) -> None:
    cover = _good_cover()
    cover.body[0] = cover.body[0] + " I am passionate about this role."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("passionate" in v for v in violations)


def test_form_letter_opener_flagged(verified: dict) -> None:
    cover = _good_cover()
    cover.body[0] = "Applying for the Full-Stack Developer position at Acme Corp."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("applying for" in v for v in violations)


def test_word_count_exceeded(verified: dict) -> None:
    cover = _good_cover()
    cover.body[0] = cover.body[0] + (" extra words" * 80)
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("words" in v for v in violations)


def test_missing_company_in_lead(verified: dict) -> None:
    cover = _good_cover()
    cover.body[0] = "I read the posting and the stack looks interesting."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("Acme Corp" in v for v in violations)


def test_too_few_paragraphs(verified: dict) -> None:
    cover = _good_cover()
    cover.body = cover.body[:2]
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("paragraph" in v for v in violations)


def test_exclamation_mark_flagged(verified: dict) -> None:
    cover = _good_cover()
    cover.body[3] = "Looking forward to chatting!"
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("exclamation" in v for v in violations)


def test_closing_diploma_recap_flagged(verified: dict) -> None:
    cover = _good_cover()
    cover.body[-1] = "My George Brown diploma and dean's list standing make me a strong candidate."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("closing recaps" in v for v in violations)


def test_unverified_number_flagged(verified: dict) -> None:
    cover = _good_cover()
    cover.body[1] = cover.body[1] + " We processed 99999 transactions daily."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("99999" in v for v in violations)


def test_unfilled_placeholder_flagged(verified: dict) -> None:
    cover = _good_cover()
    cover.body[0] = "I am applying to {company} for the {role} position."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("placeholder" in v for v in violations)
