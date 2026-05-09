"""Workday-hosted application form. *.myworkdayjobs.com.

Workday is the fiddliest mainstream ATS:
  - Most flows require an account login first; we DO NOT auto-create accounts.
    If the apply page redirects to a sign-in screen, the generic looks-like-form
    detection in autofill.py will short-circuit and we hand the page back.
  - When the form is reachable (e.g. user already signed in), Workday uses
    `data-automation-id` attributes on inputs rather than name/id. We target
    those directly; everything else falls through to the generic pass.
"""

from __future__ import annotations

from typing import Any

from jobhunt.browser.handlers._generic import generic_fill
from jobhunt.browser.handlers.types import FieldFill


async def workday_fill(page: Any, field_map: dict[str, str]) -> list[FieldFill]:
    actions: list[FieldFill] = []

    direct_targets: list[tuple[str, str]] = [
        ('input[data-automation-id="legalNameSection_firstName"]', "first_name"),
        ('input[data-automation-id="legalNameSection_lastName"]', "last_name"),
        ('input[data-automation-id="email"]', "email"),
        ('input[data-automation-id="phone-number"]', "phone"),
        ('input[data-automation-id="phoneNumber"]', "phone"),
        ('input[data-automation-id="addressSection_city"]', "city"),
        ('input[data-automation-id="linkedinQuestion"]', "linkedin"),
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

    # Resume upload — Workday uses a hidden file input under a "Select files"
    # button with data-automation-id="file-upload-input-ref".
    resume = field_map.get("resume_path")
    if resume:
        selector = 'input[data-automation-id="file-upload-input-ref"]'
        try:
            file_input = await page.query_selector(selector)
            if file_input is None:
                file_input = await page.query_selector('input[type="file"]')
            if file_input is not None:
                await file_input.set_input_files(resume)
                actions.append(
                    FieldFill(
                        selector=selector,
                        profile_key="resume_path",
                        value=resume,
                        kind="upload",
                    )
                )
        except Exception as e:  # noqa: BLE001
            actions.append(
                FieldFill(
                    selector=selector,
                    profile_key="resume_path",
                    value=resume,
                    kind="skipped",
                    note=str(e)[:80],
                )
            )

    actions.extend(await generic_fill(page, field_map))
    return actions
