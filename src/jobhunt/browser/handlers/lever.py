"""Lever-hosted application form. jobs.lever.co/<slug>/<job-id>/apply.

Lever forms are reasonably uniform across customers:
  - input[name="name"]      — full name (single field, not split)
  - input[name="email"]
  - input[name="phone"]
  - input[name="org"]       — current company
  - input[name="location"]
  - input[name="urls[LinkedIn]"], urls[GitHub], urls[Portfolio]/urls[Other]
  - input[type=file][name="resume"] — resume upload (under a "Resume/CV" card)

The generic pass picks up custom EEO/demographic questions; we add explicit
selectors here for the ones Lever names predictably.
"""

from __future__ import annotations

from typing import Any

from jobhunt.browser.handlers._generic import generic_fill
from jobhunt.browser.handlers.types import FieldFill


async def lever_fill(page: Any, field_map: dict[str, str]) -> list[FieldFill]:
    actions: list[FieldFill] = []

    direct_targets: list[tuple[str, str]] = [
        ('input[name="name"]', "full_name"),
        ('input[name="email"]', "email"),
        ('input[name="phone"]', "phone"),
        ('input[name="location"]', "city"),
        ('input[name="urls[LinkedIn]"]', "linkedin"),
        ('input[name="urls[GitHub]"]', "github"),
        ('input[name="urls[Portfolio]"]', "portfolio"),
        ('input[name="urls[Other]"]', "portfolio"),
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

    # Resume upload — Lever uses input[type=file][name="resume"] under the
    # "Resume/CV" card. Some custom embeds rename it; fall back to any file
    # input with "resume" in the name attribute.
    resume = field_map.get("resume_path")
    if resume:
        selector = 'input[type="file"][name="resume"]'
        try:
            file_input = await page.query_selector(selector)
            if file_input is None:
                file_input = await page.query_selector('input[type="file"][name*="resume"]')
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

    # Generic pass for custom questions (EEO, demographics, work auth, etc.).
    actions.extend(await generic_fill(page, field_map))
    return actions
