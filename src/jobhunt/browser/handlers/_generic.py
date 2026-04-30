"""Generic autofill — match field name/label/autocomplete against profile keys.

Best-effort fallback used when no ATS-specific handler matches. We never click
Submit; we never solve CAPTCHAs; we never log into anything.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal

from jobhunt.browser.handlers.types import FieldFill

# (profile_key, [substrings to match against name/id/label/placeholder/autocomplete])
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("first_name", ("first_name", "firstname", "given-name", "first name")),
    ("last_name", ("last_name", "lastname", "family-name", "surname", "last name")),
    ("full_name", ("full_name", "fullname", "name", "your name")),
    ("email", ("email",)),
    ("phone", ("phone", "tel", "mobile")),
    ("linkedin", ("linkedin",)),
    ("github", ("github",)),
    ("portfolio", ("portfolio", "website", "personal site", "url")),
    ("city", ("city", "locality")),
    ("region", ("state", "region", "province")),
    ("country", ("country",)),
    ("salary_expectation", ("salary", "compensation", "expected pay", "expectation")),
    ("pronouns", ("pronoun",)),
)

_NORM = re.compile(r"[^a-z0-9]+")


def _norm(s: str | None) -> str:
    return _NORM.sub(" ", (s or "").lower()).strip()


def _match(needle: str, haystacks: Iterable[str]) -> bool:
    return any(needle in h for h in haystacks if h)


def _selector_for(raw_id: str | None, raw_name: str | None, tag: str) -> str:
    if raw_id:
        return f"#{raw_id}"
    if raw_name:
        return f'[name="{raw_name}"]'
    return tag.lower() or "input"


async def generic_fill(page: Any, field_map: dict[str, str]) -> list[FieldFill]:
    actions: list[FieldFill] = []

    # Defense-in-depth: only autofill when the page has at least one <form>.
    if not await page.query_selector("form"):
        return actions

    inputs = await page.query_selector_all("form input, form textarea, form select")
    for el in inputs:
        try:
            tag = (await el.evaluate("e => e.tagName")) or ""
            input_type = (await el.evaluate("e => e.type || ''")).lower()
            if input_type in {"hidden", "submit", "button", "reset", "image"}:
                continue
            raw_name = await el.get_attribute("name")
            raw_id = await el.get_attribute("id")
            name = _norm(raw_name)
            id_ = _norm(raw_id)
            placeholder = _norm(await el.get_attribute("placeholder"))
            autocomplete = _norm(await el.get_attribute("autocomplete"))
            label = ""
            if raw_id:
                lbl_el = await page.query_selector(f"label[for='{raw_id}']")
                if lbl_el:
                    label = _norm(await lbl_el.text_content())
            haystacks = (name, id_, placeholder, autocomplete, label)

            chosen_key: str | None = None
            for key, needles in _RULES:
                if any(_match(_norm(n), haystacks) for n in needles):
                    chosen_key = key
                    break

            if input_type == "file":
                # Resume upload heuristic.
                if any("resume" in h or "cv" in h for h in haystacks):
                    chosen_key = "resume_path"
                elif any("cover" in h for h in haystacks):
                    chosen_key = "cover_letter_path"
                else:
                    continue
                value = field_map.get(chosen_key, "")
                if not value:
                    continue
                await el.set_input_files(value)
                actions.append(
                    FieldFill(
                        selector=_selector_for(raw_id, raw_name, tag),
                        profile_key=chosen_key,
                        value=value,
                        kind="upload",
                    )
                )
                continue

            if not chosen_key:
                continue
            value = field_map.get(chosen_key, "")
            if not value:
                continue

            kind: Literal["text", "upload", "select", "skipped"]
            if tag.lower() == "select":
                try:
                    await el.select_option(label=value)
                    kind = "select"
                except Exception:  # noqa: BLE001
                    actions.append(
                        FieldFill(
                            selector=_selector_for(raw_id, raw_name, tag),
                            profile_key=chosen_key,
                            value=value,
                            kind="skipped",
                            note="select option not found",
                        )
                    )
                    continue
            else:
                await el.fill(value)
                kind = "text"
            actions.append(
                FieldFill(
                    selector=_selector_for(raw_id, raw_name, tag),
                    profile_key=chosen_key,
                    value=value,
                    kind=kind,
                )
            )
        except Exception as e:  # noqa: BLE001 — never let one bad field abort the whole run
            actions.append(
                FieldFill(
                    selector="?", profile_key="?", value="", kind="skipped", note=str(e)[:80]
                )
            )
            continue
    return actions
