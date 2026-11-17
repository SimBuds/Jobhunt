"""Autofill waits for the application form (Phase B1, 2026-11-17).

A posting can open on a landing or sign-in page. The watch loop keeps checking
every open tab until a form appears, fills each form URL once, and ends when
every tab is closed. Fakes stand in for Playwright: a tab is a form when
`is_form` is set, and the loop's sleep advances a scripted timeline.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from jobhunt.browser.handlers.types import FieldFill

# The package re-exports the `autofill` function under the module's name.
autofill = importlib.import_module("jobhunt.browser.autofill")


class Tab:
    def __init__(self, url: str, *, is_form: bool = False) -> None:
        self.url = url
        self.is_form = is_form
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed


class Ctx:
    def __init__(self, *tabs: Tab) -> None:
        self.tabs = list(tabs)

    @property
    def pages(self) -> list[Tab]:
        return self.tabs


@pytest.fixture
def filled(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def detect(page: Tab) -> bool:
        return page.is_form

    async def handler(page: Tab, field_map: dict[str, str]) -> list[FieldFill]:
        calls.append(page.url)
        return [FieldFill(selector="#first", profile_key="first_name", value="Jane")]

    monkeypatch.setattr(autofill, "looks_like_application_page", detect)
    monkeypatch.setattr(autofill, "pick_handler", lambda url: ("fake", handler))
    return calls


def _timeline(*steps: Any):
    """A sleep that runs one scripted step per tick, then closes every tab."""
    queue = list(steps)

    async def sleep(_: float, ctx: Ctx) -> None:
        if queue:
            queue.pop(0)()
        else:
            for t in ctx.tabs:
                t.closed = True

    return sleep


async def _run(ctx: Ctx, tmp_path: Path, *steps: Any) -> dict[str, Any]:
    sleep = _timeline(*steps)
    plan_path = tmp_path / "fill-plan.json"
    await autofill._watch(ctx, {"first_name": "Jane"}, plan_path, sleep=lambda s: sleep(s, ctx))
    return json.loads(plan_path.read_text())


async def test_form_on_first_load_fills_at_once(tmp_path: Path, filled: list[str]) -> None:
    plan = await _run(Ctx(Tab("https://ats/apply", is_form=True)), tmp_path)
    assert filled == ["https://ats/apply"]
    assert plan["handler"] == "fake"
    assert len(plan["fills"]) == 1


async def test_landing_then_form_on_same_tab(tmp_path: Path, filled: list[str]) -> None:
    tab = Tab("https://ats/job/1")

    def sign_in_done() -> None:
        tab.url, tab.is_form = "https://ats/apply/1", True

    await _run(Ctx(tab), tmp_path, sign_in_done)
    assert filled == ["https://ats/apply/1"]


async def test_form_in_a_new_tab(tmp_path: Path, filled: list[str]) -> None:
    ctx = Ctx(Tab("https://ats/job/1"))
    await _run(ctx, tmp_path, lambda: ctx.tabs.append(Tab("https://ats/apply/1", is_form=True)))
    assert filled == ["https://ats/apply/1"]


async def test_two_steps_fill_once_each_and_accumulate(tmp_path: Path, filled: list[str]) -> None:
    tab = Tab("https://ats/apply/step1", is_form=True)

    def next_step() -> None:
        tab.url = "https://ats/apply/step2"

    plan = await _run(Ctx(tab), tmp_path, lambda: None, next_step, lambda: None)
    assert filled == ["https://ats/apply/step1", "https://ats/apply/step2"]
    assert len(plan["fills"]) == 2
    assert [s["url"] for s in plan["steps"]] == filled


async def test_sign_in_page_never_fills(tmp_path: Path, filled: list[str]) -> None:
    plan = await _run(Ctx(Tab("https://ats/login")), tmp_path, lambda: None, lambda: None)
    assert filled == []
    assert plan["handler"] == "none"
    assert "No application form" in plan["warning"]


async def test_closing_with_no_tabs_ends_cleanly(tmp_path: Path, filled: list[str]) -> None:
    plan = await _run(Ctx(), tmp_path)
    assert filled == [] and plan["handler"] == "none"


async def test_a_tab_that_errors_mid_check_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filled: list[str]
) -> None:
    tab = Tab("https://ats/apply/1", is_form=True)
    tries = {"n": 0}

    async def flaky(page: Tab) -> bool:
        tries["n"] += 1
        if tries["n"] == 1:
            raise RuntimeError("Execution context was destroyed")
        return page.is_form

    monkeypatch.setattr(autofill, "looks_like_application_page", flaky)
    await _run(Ctx(tab), tmp_path, lambda: None)
    assert filled == ["https://ats/apply/1"]
