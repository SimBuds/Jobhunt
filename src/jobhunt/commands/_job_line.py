"""One readable line per job for human output (Phase O1, 2026-11-10).

Lists used to lead each row with the internal job id
(`lever:acme:230b8d92-...`, up to 57 characters), which pushed the title off
the screen. A human scanning a list needs the score, the title and the
company; the id belongs behind `--verbose`. `--json` payloads are unaffected:
this module only shapes text meant for a person.
"""

from __future__ import annotations

import shutil
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SEPARATOR = " · "
_ELLIPSIS = "…"
_DECLINED = "  declined"


def display_url(url: str | None) -> str:
    """`url` without its `utm_*` query parameters, for printing only.

    Adzuna's `redirect_url` carries `utm_source=<API app id>`. The stored
    URL keeps it, because Adzuna attributes clicks with it and autofill opens
    the stored link; a printed copy (a screenshot, a pasted log) should not
    carry the account id (Phase O6, 2026-11-10)."""
    if not url:
        return ""
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def job_line_label(title: str | None, company: str | None, location: str | None = None) -> str:
    """'Title · Company · Location', skipping whichever part is missing."""
    return _SEPARATOR.join(part for part in (title, company, location) if part)


def job_line(
    *,
    title: str | None,
    company: str | None,
    score: int | None,
    declined: bool = False,
    location: str | None = None,
    tags: str = "",
    width: int | None = None,
) -> str:
    """' 72  Title · Company · Location  tags', fitted to `width` (the
    terminal by default). Only the label is cut, so `tags` (status, verdict)
    and the declined marker always stay readable.

    A declined job shows `--` in place of its score and a trailing
    "declined"; the reason stays with `--verbose`, where it is useful."""
    shown = "--" if declined or score is None else str(score)
    head = f"{shown:>3}  "
    body = job_line_label(title, company, location)
    tail = (f"  {tags}" if tags else "") + (_DECLINED if declined else "")
    limit = width if width is not None else shutil.get_terminal_size((100, 24)).columns
    room = limit - len(head) - len(tail)
    if len(body) > room:
        body = body[: max(room - len(_ELLIPSIS), 0)].rstrip() + _ELLIPSIS
    return f"{head}{body}{tail}"
