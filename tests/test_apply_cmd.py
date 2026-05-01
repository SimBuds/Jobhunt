from __future__ import annotations

from jobhunt.commands.apply_cmd import _company_slug


def test_company_slug_strips_inc_suffix():
    assert _company_slug("Astra North Infoteck Inc.") == "Astra_North_Infoteck"


def test_company_slug_strips_legal_suffixes():
    assert _company_slug("Acme LLC") == "Acme"
    assert _company_slug("Beta Corp.") == "Beta"
    assert _company_slug("Gamma Limited") == "Gamma"
    assert _company_slug("Delta Pty Ltd") == "Delta"
    assert _company_slug("Epsilon GmbH") == "Epsilon"


def test_company_slug_collapses_punctuation():
    assert _company_slug("Foo, Bar & Baz!") == "Foo_Bar_Baz"


def test_company_slug_handles_empty_and_none():
    assert _company_slug(None) == "Company"
    assert _company_slug("") == "Company"
    assert _company_slug("   ") == "Company"
    assert _company_slug("Inc.") == "Company"


def test_company_slug_caps_length():
    long = "A" * 80
    out = _company_slug(long)
    assert len(out) <= 40


def test_company_slug_truncates_at_underscore_boundary():
    out = _company_slug("Some Very Long Multi Word Company Name That Goes On And On Forever Inc")
    assert len(out) <= 40
    assert not out.endswith("_")
