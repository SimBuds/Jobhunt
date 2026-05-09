"""Ashby-hosted application form. jobs.ashbyhq.com/<slug>/<job-id>/application.

Ashby system fields use predictable `_systemfield_*` ids. Custom questions live
under dynamic ids and are picked up by the generic pass.
"""

from __future__ import annotations

from typing import Any

from jobhunt.browser.handlers._generic import generic_fill
from jobhunt.browser.handlers.types import FieldFill


async def ashby_fill(page: Any, field_map: dict[str, str]) -> list[FieldFill]:
    actions: list[FieldFill] = []

    direct_targets: list[tuple[str, str]] = [
        ("input#_systemfield_name", "full_name"),
        ("input#_systemfield_email", "email"),
        ("input#_systemfield_phoneNumber", "phone"),
        ("input#_systemfield_location", "city"),
        ("input#_systemfield_linkedinUrl", "linkedin"),
        ("input#_systemfield_githubUrl", "github"),
        ("input#_systemfield_websiteUrl", "portfolio"),
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

    # Resume upload — Ashby renders a hidden file input under the resume card.
    resume = field_map.get("resume_path")
    if resume:
        selector = 'input[type="file"][name*="resume" i]'
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
