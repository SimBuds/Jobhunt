"""Ad-hoc job ingestion: a single URL becomes a `Job`.

This is the only ingest module that is user-initiated (not bulk-scanned). It
exists so the user can run `jobhunt apply --url <link>` for a posting that
wasn't surfaced by the scan adapters.

Scope deliberately small:
  - **Playwright** for the actual fetch — most modern ATS career portals
    (Workday, Phenom People, iCIMS, SuccessFactors) are JS-rendered SPAs, so
    a plain httpx GET returns only the static shell. Reuses the same
    Playwright dep already pulled in for `browser/autofill.py`.
  - stdlib `html.parser` for body extraction from the rendered HTML.
  - stdlib `urllib.robotparser` for the robots.txt check.
  - no DB writes here; the caller persists via `db.upsert_job`.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from jobhunt.errors import IngestError
from jobhunt.models import Job

# Tags whose content is chrome, not job copy.
_SKIP_TAGS: frozenset[str] = frozenset({
    "script", "style", "nav", "header", "footer", "aside", "noscript", "form",
    "button", "svg",
})
_BLOCK_TAGS: frozenset[str] = frozenset({
    "p", "div", "section", "article", "li", "ul", "ol", "br", "h1", "h2",
    "h3", "h4", "h5", "h6", "tr", "td", "th",
})

_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINE_RUN_RE = re.compile(r"\n{3,}")


class _BodyExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        role = next((v for k, v in attrs if k == "role"), None)
        if role == "navigation":
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [_WHITESPACE_RE.sub(" ", ln).strip() for ln in raw.splitlines()]
        joined = "\n".join(ln for ln in lines if ln) + "\n"
        return _BLANK_LINE_RUN_RE.sub("\n\n", joined).strip()


def _extract_body_text(html: str) -> str:
    p = _BodyExtractor()
    p.feed(html)
    return p.text()


class _MetadataExtractor(HTMLParser):
    """Pull `<title>`, `og:title`, `og:site_name` for title/company hints."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.og_title: str | None = None
        self.og_site_name: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            ad = dict(attrs)
            prop = (ad.get("property") or ad.get("name") or "").lower()
            content = ad.get("content")
            if not content:
                return
            if prop == "og:title":
                self.og_title = content.strip()
            elif prop == "og:site_name":
                self.og_site_name = content.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            cleaned = data.strip()
            if cleaned:
                self.title = cleaned


def _clean_title(title: str) -> str:
    """Strip ATS-injected location / category cruft so the title is just the role.

    BMO's Phenom page emits a `<title>` like
    `AI Engineer/Developer in Toronto, ON M8X 1C4, Canada | Technology at Bank of Montreal`.
    The " at <company>" split happens separately; this strips the noisy left
    side: `" in <City/State/Country>"` and `" | <Category>"` fragments.
    """
    # Drop " in <Location>" — everything from " in " up to the next pipe or end.
    title = re.sub(r"\s+in\s+[^|]+?(?=\s*\||$)", "", title, flags=re.IGNORECASE)
    # Drop everything after " | " (category, brand, etc.) but keep the role.
    title = title.split(" | ", 1)[0]
    return title.strip(" -—|·")


def _extract_metadata(html: str) -> tuple[str | None, str | None]:
    """Return (title, company) best-effort from `<title>`/OG tags.

    Prefers the " at <Company>" split inside og:title when present, because
    og:site_name is often a section/category label ("Product Management
    Careers at Intuit") rather than the company itself. The split is only
    skipped if og:site_name looks like a clean company name (no " at ").
    """
    p = _MetadataExtractor()
    p.feed(html)
    title = p.og_title or p.title
    company = p.og_site_name

    # Try the " at " / " @ " split first — it usually gives a cleaner company
    # than og:site_name, which platforms like Phenom emit as a category page
    # ("Product Management Careers at Intuit"). Only fall back to the OG
    # site_name if the title doesn't contain a " at " marker.
    if title:
        for sep in (" at ", " @ "):
            if sep in title:
                lhs, rhs = title.rsplit(sep, 1)
                role = lhs.strip()
                co = rhs.split(" · ")[0].split(" | ")[0].strip()
                if role and co:
                    title = role
                    company = co
                break
        else:
            # No " at " in title — try the weaker separators only if og:site_name
            # wasn't supplied at all.
            if not company:
                for sep in (" — ", " - ", " | "):
                    if sep in title:
                        lhs, rhs = title.split(sep, 1)
                        title = lhs.strip() or title
                        company = rhs.split(" · ")[0].split(" | ")[0].strip()
                        break

    if title:
        title = _clean_title(title)
    return (title or None), (company or None)


def _synth_id(url: str | None, title: str | None, company: str | None, description: str) -> str:
    payload = url or f"{title or ''}|{company or ''}|{description[:500]}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"manual:{digest}"


def robots_allowed(url: str, user_agent: str) -> bool:
    """True if robots.txt allows this URL for this UA. Returns True on any
    fetch error — a missing/unreachable robots file is not a denial."""
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return True
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


async def _fetch_rendered_html(
    url: str, *, user_agent: str, timeout_ms: int = 30_000
) -> tuple[str, str]:
    """Load `url` in headless Chromium, return (rendered HTML, final URL).

    Waits for `networkidle` so JS-rendered SPAs (Workday / Phenom / iCIMS /
    SuccessFactors) have time to hydrate. A best-effort `networkidle` is
    capped so a chatty analytics tail doesn't block forever — we fall back
    to whatever the page has by then.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise IngestError(
            "playwright is not installed. Run `uv sync` then `uv run playwright install chromium`."
        ) from e

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(user_agent=user_agent)
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass  # analytics tail timed out — content is already there.
                html = await page.content()
                final_url = page.url
            finally:
                await ctx.close()
        finally:
            await browser.close()
    return html, final_url


async def fetch_url_as_job(
    url: str,
    *,
    user_agent: str,
    title_override: str | None = None,
    company_override: str | None = None,
) -> Job:
    """Render `url` via Playwright, extract title/company/body, return a Job."""
    html, final_url = await _fetch_rendered_html(url, user_agent=user_agent)

    extracted_title, extracted_company = _extract_metadata(html)
    title = title_override or extracted_title
    company = company_override or extracted_company
    description = _extract_body_text(html)
    if not description:
        raise IngestError(f"no body text extracted from {url}")

    job_id = _synth_id(url, title, company, description)
    return Job(
        id=job_id,
        source="manual",
        external_id=job_id.split(":", 1)[1],
        company=company,
        title=title,
        location=None,
        remote_type="unknown",
        description=description,
        url=final_url,
    )


