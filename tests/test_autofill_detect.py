"""Tests for autofill page detection and selector preservation."""

from __future__ import annotations

import pytest

from jobhunt.browser.autofill import looks_like_application_page
from jobhunt.browser.handlers._generic import generic_fill


class FakeEl:
    def __init__(
        self,
        *,
        attrs: dict[str, str] | None = None,
        tag: str = "INPUT",
        type_: str = "text",
        text: str = "",
    ):
        self._attrs = attrs or {}
        self._tag = tag
        self._type = type_
        self._text = text
        self.filled: str | None = None

    async def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)

    async def evaluate(self, expr: str) -> str:
        if "tagName" in expr:
            return self._tag
        if "type" in expr:
            return self._type
        return ""

    async def text_content(self) -> str:
        return self._text

    async def fill(self, value: str) -> None:
        self.filled = value


class FakePage:
    def __init__(
        self,
        *,
        all_inputs: list[FakeEl] | None = None,
        form_inputs: list[FakeEl] | None = None,
        file_inputs_in_form: list[FakeEl] | None = None,
        textareas: list[FakeEl] | None = None,
        first_match: dict[str, FakeEl] | None = None,
        has_form: bool = True,
    ):
        self._form_inputs = form_inputs or []
        self._file_inputs_in_form = file_inputs_in_form or []
        self._textareas = textareas or []
        self._first_match = first_match or {}
        self._has_form = has_form

    async def query_selector(self, selector: str) -> FakeEl | None:
        if selector == "form":
            return FakeEl() if self._has_form else None
        return self._first_match.get(selector)

    async def query_selector_all(self, selector: str) -> list[FakeEl]:
        if selector == "form input[type='file']":
            return self._file_inputs_in_form
        if selector == "textarea":
            return self._textareas
        if selector.startswith("form "):
            return self._form_inputs
        return []


@pytest.mark.asyncio
async def test_detects_resume_file_input():
    page = FakePage(file_inputs_in_form=[FakeEl(attrs={"name": "resume_upload"}, type_="file")])
    assert await looks_like_application_page(page) is True


@pytest.mark.asyncio
async def test_detects_given_name_autocomplete():
    page = FakePage(first_match={"input[autocomplete='given-name']": FakeEl()})
    assert await looks_like_application_page(page) is True


@pytest.mark.asyncio
async def test_detects_cover_letter_textarea():
    page = FakePage(textareas=[FakeEl(attrs={"placeholder": "Cover letter"}, tag="TEXTAREA")])
    assert await looks_like_application_page(page) is True


@pytest.mark.asyncio
async def test_rejects_listing_page_with_search_inputs_only():
    page = FakePage()  # no resume input, no autocomplete, no cover-letter textarea
    assert await looks_like_application_page(page) is False


@pytest.mark.asyncio
async def test_generic_fill_preserves_dashed_selector():
    el = FakeEl(attrs={"id": "email-alert", "name": "email-alert"})
    page = FakePage(form_inputs=[el], has_form=True)
    actions = await generic_fill(page, {"email": "casey@example.com"})
    assert len(actions) == 1
    assert actions[0].selector == "#email-alert"
    assert actions[0].value == "casey@example.com"
    assert el.filled == "casey@example.com"


@pytest.mark.asyncio
async def test_generic_fill_skips_when_no_form():
    el = FakeEl(attrs={"id": "email", "name": "email"})
    page = FakePage(form_inputs=[el], has_form=False)
    actions = await generic_fill(page, {"email": "casey@example.com"})
    assert actions == []
