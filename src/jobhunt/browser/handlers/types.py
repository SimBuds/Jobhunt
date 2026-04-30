"""Shared types for autofill handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class FieldFill:
    """A single planned action against the page."""

    selector: str
    profile_key: str
    value: str
    kind: Literal["text", "upload", "select", "skipped"] = "text"
    note: str = ""


# A handler accepts (page, field_map) and returns the FieldFills it performed.
Handler = Callable[[Any, dict[str, str]], Awaitable[list[FieldFill]]]
