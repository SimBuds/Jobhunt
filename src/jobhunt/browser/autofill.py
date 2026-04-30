"""Open the application URL in a headed browser and run the matching handler.

Hard rules (from CLAUDE.md):
- Never click Submit. Hand the browser to the human.
- Never auto-create accounts. If signup is required, exit with a notice.
- Log every planned fill to fill-plan.json for auditability.
- Run headed by default.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jobhunt.browser.handlers import pick_handler
from jobhunt.browser.profile_map import build_field_map
from jobhunt.config import ApplicantProfile
from jobhunt.errors import BrowserError


_FORM_FIELD_HINT_ATTRS = ("name", "id", "placeholder", "aria-label")


async def looks_like_application_page(page: Any) -> bool:
    """Heuristically decide whether the page hosts an application form.

    True if any of:
      - a <form> contains an <input type="file"> whose attrs hint at resume/cv;
      - any <input> has autocomplete=given-name/family-name;
      - any <textarea> hints at "cover letter".
    """
    if await page.query_selector("input[autocomplete='given-name']"):
        return True
    if await page.query_selector("input[autocomplete='family-name']"):
        return True

    for el in await page.query_selector_all("form input[type='file']"):
        try:
            for attr in _FORM_FIELD_HINT_ATTRS:
                v = (await el.get_attribute(attr)) or ""
                if "resume" in v.lower() or "cv" in v.lower():
                    return True
        except Exception:  # noqa: BLE001
            continue

    for ta in await page.query_selector_all("textarea"):
        try:
            for attr in _FORM_FIELD_HINT_ATTRS:
                v = (await ta.get_attribute(attr)) or ""
                if "cover letter" in v.lower():
                    return True
        except Exception:  # noqa: BLE001
            continue

    return False


async def autofill(
    *,
    url: str,
    profile: ApplicantProfile,
    resume_path: Path,
    cover_path: Path,
    out_dir: Path,
    headed: bool = True,
    user_data_dir: Path | None = None,
) -> Path:
    """Launch a browser, navigate to the application URL, run the handler, write fill-plan.json.

    Returns the path to the fill-plan.json. Leaves the browser open until user
    closes it.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise BrowserError(
            "playwright is not installed. Run `uv sync` then `uv run playwright install chromium`."
        ) from e

    field_map = build_field_map(profile, resume_path=resume_path, cover_path=cover_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / "fill-plan.json"

    handler_name, handler = pick_handler(url)

    async with async_playwright() as pw:
        if user_data_dir:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir), headless=not headed
            )
            browser = None
        else:
            browser = await pw.chromium.launch(headless=not headed)
            ctx = await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception as e:  # noqa: BLE001
            await ctx.close()
            if browser:
                await browser.close()
            raise BrowserError(f"failed to load {url}: {e}") from e

        if not await looks_like_application_page(page):
            plan = {
                "url": url,
                "handler": "none",
                "fills": [],
                "field_map_keys": sorted(field_map.keys()),
                "warning": (
                    "No application form detected on this page. This URL is likely a "
                    "job listing — find the 'Apply' link and open the employer's "
                    "application page manually. Browser left open."
                ),
            }
            plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        else:
            actions = await handler(page, field_map)
            plan = {
                "url": url,
                "handler": handler_name,
                "fills": [asdict(a) for a in actions],
                "field_map_keys": sorted(field_map.keys()),
                "warning": "Browser left open. You must review and click Submit yourself.",
            }
            plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        # Wait for the user to close the browser.
        with contextlib.suppress(Exception):
            await page.wait_for_event("close", timeout=0)
        if browser:
            await browser.close()

    return plan_path
