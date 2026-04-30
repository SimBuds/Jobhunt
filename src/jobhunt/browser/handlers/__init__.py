"""ATS-specific autofill handlers. Each returns a list of `FieldFill` actions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from jobhunt.browser.handlers._generic import generic_fill
from jobhunt.browser.handlers.greenhouse import greenhouse_fill
from jobhunt.browser.handlers.types import FieldFill, Handler

# Domain → handler. Falls back to `generic_fill`.
_BY_HOST: dict[str, Handler] = {
    "boards.greenhouse.io": greenhouse_fill,
    "job-boards.greenhouse.io": greenhouse_fill,
}


def pick_handler(url: str) -> tuple[str, Handler]:
    host = (urlparse(url).hostname or "").lower()
    for needle, handler in _BY_HOST.items():
        if needle in host:
            return needle, handler
    return "generic", generic_fill


__all__ = [
    "FieldFill",
    "Handler",
    "Callable",
    "Awaitable",
    "pick_handler",
    "generic_fill",
    "greenhouse_fill",
]
