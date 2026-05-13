"""Greenhouse-hosted application form. Public boards.greenhouse.io / job-boards.greenhouse.io."""

from __future__ import annotations

from typing import Any

from jobhunt.browser.handlers._generic import generic_fill
from jobhunt.browser.handlers.types import FieldFill


async def greenhouse_fill(page: Any, field_map: dict[str, str]) -> list[FieldFill]:
    """Greenhouse forms have well-named ids: first_name, last_name, email, phone, etc.

    We delegate the heavy lifting to `generic_fill` (whose rules already cover
    the standard fields) and add explicit selectors for the few greenhouse
    quirks: resume drag-drop area and the LinkedIn URL field.
    """
    actions: list[FieldFill] = []

    direct_targets = [
        ("input#first_name", "first_name"),
        ("input#last_name", "last_name"),
        ("input#email", "email"),
        ("input#phone", "phone"),
        ('input[name="job_application[answers_attributes][0][text_value]"]', "linkedin"),
    ]
    for selector, key in direct_targets:
        value = field_map.get(key)
        if not value:
            continue
        try:
            el = await page.query_selector(selector)
            if not el:
                continue
            await el.fill(value)
            actions.append(FieldFill(selector=selector, profile_key=key, value=value))
        except Exception as e:  # noqa: BLE001
            actions.append(
                FieldFill(
                    selector=selector,
                    profile_key=key,
                    value=value,
                    kind="skipped",
                    note=str(e)[:80],
                )
            )

    # Resume upload — Greenhouse uses a hidden <input type="file"> under "Attach"
    resume = field_map.get("resume_path")
    if resume:
        try:
            file_input = await page.query_selector('input[type="file"][name*="resume"]')
            if file_input is None:
                file_input = await page.query_selector('input[type="file"]')
            if file_input is not None:
                await file_input.set_input_files(resume)
                actions.append(
                    FieldFill(
                        selector='input[type="file"]',
                        profile_key="resume_path",
                        value=resume,
                        kind="upload",
                    )
                )
        except Exception as e:  # noqa: BLE001
            actions.append(
                FieldFill(
                    selector="?",
                    profile_key="resume_path",
                    value=resume,
                    kind="skipped",
                    note=str(e)[:80],
                )
            )

    # Generic pass picks up everything else (custom EEO questions, etc.).
    actions.extend(await generic_fill(page, field_map))
    return actions
