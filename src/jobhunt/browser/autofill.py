"""Open the application URL in a headed browser and run the matching handler.

Hard rules (from AGENTS.md):
- Never click Submit. Hand the browser to the human.
- Never auto-create accounts. If signup is required, exit with a notice.
- Log every planned fill to fill-plan.json for auditability.
- Run headed by default.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import ViewportSize

from jobhunt.browser.handlers import pick_handler
from jobhunt.browser.profile_map import build_field_map
from jobhunt.config import ApplicantProfile
from jobhunt.errors import BrowserError

_FORM_FIELD_HINT_ATTRS = ("name", "id", "placeholder", "aria-label")
# A whole attribute value naming a first or last name field, in any of the
# usual spellings: firstName, first_name, first-name, "Last Name".
_NAME_FIELD_RE = re.compile(r"\s*(?:first|last|given|family)[\s_-]?name\s*\*?\s*", re.IGNORECASE)
_POLL_SECONDS = 2.0
_NO_FORM_WARNING = (
    "No application form detected yet. Sign in or click Apply in the browser; "
    "autofill fills each form step as it appears. Browser left open."
)
_SUBMIT_WARNING = "Browser left open. You must review and click Submit yourself."
_BROWSER_VIEWPORT: ViewportSize = {"width": 1440, "height": 1000}
_BROWSER_WINDOW_SIZE = "--window-size=1440,1000"


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

    # Oracle-style forms carry no autocomplete hints, only an id, name or
    # label such as "firstName" (2026-11-17). A sign-in page has no name field.
    for el in await page.query_selector_all("input"):
        try:
            for attr in _FORM_FIELD_HINT_ATTRS:
                if _NAME_FIELD_RE.fullmatch((await el.get_attribute(attr)) or ""):
                    return True
        except Exception:  # noqa: BLE001
            continue

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
    """Launch a browser at the application URL and fill forms as they appear.

    Returns the path to fill-plan.json once the user closes the browser. The
    posting may first show a landing or sign-in page; `_watch` keeps checking
    every tab until the user is done.
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

    async with async_playwright() as pw:
        if user_data_dir:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=not headed,
                viewport=_BROWSER_VIEWPORT,
                args=[_BROWSER_WINDOW_SIZE],
            )
            browser = None
        else:
            browser = await pw.chromium.launch(headless=not headed, args=[_BROWSER_WINDOW_SIZE])
            ctx = await browser.new_context(viewport=_BROWSER_VIEWPORT)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:  # noqa: BLE001
            await ctx.close()
            if browser:
                await browser.close()
            raise BrowserError(f"failed to load {url}: {e}") from e
        # SPA-heavy ATS pages (Intuit, Workday, iCIMS) fire analytics requests
        # long after the form is visible. A chatty tail should not abort the
        # autofill session.
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=10_000)

        # Keep watching until the user closes the browser: the posting may
        # open on a landing or sign-in page, and the form only appears after
        # the user signs in or clicks Apply, possibly in a new tab.
        await _watch(ctx, field_map, plan_path)
        if browser:
            await browser.close()

    return plan_path


def _write_plan(plan_path: Path, plan: dict[str, Any]) -> None:
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")


async def _watch(
    ctx: Any,
    field_map: dict[str, str],
    plan_path: Path,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Fill each application form as it appears, until every tab is closed.

    Every `_POLL_SECONDS` it checks each open tab, newest first. A tab whose
    URL already filled is skipped, so a multi-step form fills each step once.
    The handler is picked from the tab's current URL, because Adzuna and
    sign-in pages redirect before the form loads. Fills accumulate into
    `fill-plan.json`, rewritten after each step. Nothing is ever clicked.
    """
    plan: dict[str, Any] = {
        "url": None,
        "handler": "none",
        "steps": [],
        "fills": [],
        "field_map_keys": sorted(field_map.keys()),
        "warning": _NO_FORM_WARNING,
    }
    _write_plan(plan_path, plan)
    done: set[str] = set()
    while True:
        try:
            pages = [p for p in ctx.pages if not p.is_closed()]
        except Exception:  # noqa: BLE001 - the browser itself was closed
            pages = []
        if not pages:
            return plan
        for page in reversed(pages):
            url = page.url or ""
            if url in done:
                continue
            try:
                if not await looks_like_application_page(page):
                    continue
                handler_name, handler = pick_handler(url)
                actions = await handler(page, field_map)
            except Exception:  # noqa: BLE001
                # The tab navigated or closed mid-check. It is not marked
                # done, so the next tick checks it again.
                continue
            done.add(url)
            fills = [asdict(a) for a in actions]
            plan["fills"].extend(fills)
            plan["steps"].append({"url": url, "handler": handler_name, "fills": len(fills)})
            plan.update(url=url, handler=handler_name, warning=_SUBMIT_WARNING)
            _write_plan(plan_path, plan)
        await sleep(_POLL_SECONDS)
