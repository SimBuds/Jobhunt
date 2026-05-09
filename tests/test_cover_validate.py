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


def test_scalable_does_not_trigger_scala_fabrication(verified: dict) -> None:
    cover = _good_cover()
    cover.body[1] = cover.body[1] + " I write scalable e-commerce solutions for clients."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert not any("scala" in v.lower() for v in violations)


def test_disclaimed_tech_does_not_fire_fabrication(verified: dict) -> None:
    cover = _good_cover()
    cover.body[2] = "I focus on JavaScript and TypeScript rather than Scala or Kotlin for back-end work."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert not any("unverified tech claim" in v for v in violations)


def test_claimed_tech_still_fires_fabrication(verified: dict) -> None:
    cover = _good_cover()
    cover.body[2] = "I have shipped production Kafka pipelines for client analytics."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("kafka" in v.lower() for v in violations)


def test_company_with_separator_matched_partially(verified: dict) -> None:
    cover = _good_cover(company="PheedLoop")
    violations = validate_cover(
        cover, verified=verified, company="PheedLoop / NordSpace", max_words=280
    )
    assert not any("does not name company" in v for v in violations)


def test_company_descriptor_suffix_dropped(verified: dict) -> None:
    """Real-world miss: 'Appnovation Technologies' lead used 'Appnovation' only."""
    cover = _good_cover(company="Appnovation")
    violations = validate_cover(
        cover, verified=verified, company="Appnovation Technologies", max_words=280
    )
    assert not any("does not name company" in v for v in violations)


def test_company_with_tld_suffix_matches_root(verified: dict) -> None:
    """Real-world miss: 'SRED.io' lead wrote 'SRED'."""
    cover = _good_cover(company="SRED")
    violations = validate_cover(
        cover, verified=verified, company="SRED.io", max_words=280
    )
    assert not any("does not name company" in v for v in violations)


def test_company_with_inc_suffix_matches_partial(verified: dict) -> None:
    """Real-world miss: 'Astra North Infoteck Inc.' lead wrote 'Astra North'."""
    cover = _good_cover(company="Astra North")
    violations = validate_cover(
        cover, verified=verified, company="Astra North Infoteck Inc.", max_words=280
    )
    assert not any("does not name company" in v for v in violations)


def test_company_match_still_fails_when_absent(verified: dict) -> None:
    """Sanity: relaxing the match must not stop firing when the lead is silent."""
    cover = _good_cover()
    cover.body[0] = "I read the posting and the stack looks interesting."
    violations = validate_cover(
        cover, verified=verified, company="Appnovation Technologies", max_words=280
    )
    assert any("does not name company" in v for v in violations)


def test_unverified_number_in_lead_paragraph_allowed(verified: dict) -> None:
    cover = _good_cover()
    cover.body[0] = "Acme Corp powers marketing for 1,500 events across the country, and your engineering work directly addresses problems I've solved on Shopify and HubSpot."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert not any("1,500" in v or "1500" in v for v in violations)


def test_unverified_number_in_middle_paragraph_still_flagged(verified: dict) -> None:
    cover = _good_cover()
    cover.body[1] = cover.body[1] + " I have shipped 9,999 features in my career."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("9,999" in v or "9999" in v for v in violations)


def test_defensive_rather_than_phrasing_flagged(verified: dict) -> None:
    """Regression: covers were volunteering gaps with 'rather than' / 'the
    model transfers' phrasing (cover.md §4 + §8)."""
    cover = _good_cover()
    cover.body[2] = (
        "I am familiar with Java and Spring Boot rather than those directly, "
        "but the model transfers from my nine years leading culinary teams."
    )
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("the model transfers" in v.lower() for v in violations) or any(
        "defensive" in v.lower() for v in violations
    )


def test_neutral_rather_than_not_flagged(verified: dict) -> None:
    """A neutral 'rather than' (not disclaiming a tech) should pass."""
    cover = _good_cover()
    cover.body[1] = (
        cover.body[1] + " I prefer concrete examples rather than abstract claims."
    )
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert not any("rather than" in v.lower() for v in violations)


def test_es6_in_body_does_not_flag_unverified_number(verified: dict) -> None:
    """Regression: 'ES6+' was being parsed as the digit cluster '6' and flagged.

    The digit-cluster regex must skip digits embedded in alphanumeric tokens
    like ES6, v8, ES2015. Only standalone numbers like '30%' or '200+' should
    be subject to verification.
    """
    cover = _good_cover()
    cover.body[1] = "I use JavaScript ES6+ and TypeScript daily for client work."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert not any("unverified number: '6'" in v for v in violations)


def test_unfilled_placeholder_flagged(verified: dict) -> None:
    cover = _good_cover()
    cover.body[0] = "I am applying to {company} for the {role} position."
    violations = validate_cover(cover, verified=verified, company="Acme Corp", max_words=280)
    assert any("placeholder" in v for v in violations)
