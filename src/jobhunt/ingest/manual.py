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

import contextlib
import hashlib
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from jobhunt.errors import IngestError
from jobhunt.models import Job

# Anything shorter than this after extraction is treated as a render failure
# rather than a real posting. ~400 chars is roughly two short paragraphs —
# below that we'd be scoring against the page title and assorted chrome.
MIN_BODY_CHARS = 400

# Tags whose content is chrome, not job copy.
# Note: `form` is intentionally NOT skipped — many ASP.NET WebForms career
# portals (e.g. Insight Global) wrap the entire page in a single
# `<form id="form1" runat="server">`, so skipping it would drop 100 % of the
# JD body. The cost of including stray field labels on application pages is
# small compared to the benefit of WebForms content actually landing.
_SKIP_TAGS: frozenset[str] = frozenset({
    "script", "style", "nav", "header", "footer", "aside", "noscript",
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


class _JsonLdCollector(HTMLParser):
    """Collect the text inside every `<script type="application/ld+json">`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._in_jsonld = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        type_attr = next((v for k, v in attrs if k == "type"), None) or ""
        if type_attr.strip().lower() == "application/ld+json":
            self._in_jsonld = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            text = "".join(self._buf).strip()
            if text:
                self.blocks.append(text)
            self._in_jsonld = False
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._buf.append(data)


def _flatten_jsonld_nodes(payload: Any) -> list[dict[str, Any]]:
    """Walk a parsed JSON-LD payload and return every dict-shaped node, so we
    can find a `JobPosting` whether it lives at the root, inside `@graph`, or
    inside a top-level list."""
    out: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        out.append(payload)
        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                out.extend(_flatten_jsonld_nodes(item))
    elif isinstance(payload, list):
        for item in payload:
            out.extend(_flatten_jsonld_nodes(item))
    return out


def _is_jobposting(node: dict[str, Any]) -> bool:
    t = node.get("@type")
    if isinstance(t, str):
        return t == "JobPosting"
    if isinstance(t, list):
        return "JobPosting" in t
    return False


def _strip_html(html_fragment: str) -> str:
    """Flatten an HTML-encoded string (as JobPosting.description usually is)
    to plain text using the same chrome-aware extractor used on full pages."""
    return _extract_body_text(html_fragment)


def _jobposting_company(node: dict[str, Any]) -> str | None:
    org = node.get("hiringOrganization")
    if isinstance(org, dict):
        name = org.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    if isinstance(org, str) and org.strip():
        return org.strip()
    return None


def _jobposting_location(node: dict[str, Any]) -> str | None:
    loc = node.get("jobLocation")
    if isinstance(loc, list):
        loc = next((item for item in loc if isinstance(item, dict)), None)
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address")
    if isinstance(addr, dict):
        parts = [
            addr.get("addressLocality"),
            addr.get("addressRegion"),
            addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else None,
        ]
        joined = ", ".join(p for p in parts if isinstance(p, str) and p.strip())
        if joined:
            return joined
    name = loc.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _extract_jsonld_jobposting(html: str) -> dict[str, Any] | None:
    """Return the first `JobPosting` JSON-LD node embedded in `html`, or None
    if no parseable JobPosting block is found. Tolerant to invalid JSON in
    other ld+json blocks on the page."""
    collector = _JsonLdCollector()
    collector.feed(html)
    for block in collector.blocks:
        try:
            payload = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        for node in _flatten_jsonld_nodes(payload):
            if _is_jobposting(node):
                return node
    return None


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


# Selectors that commonly wrap a JD body across the major ATS portals. We
# wait for ANY of these to appear before grabbing HTML so lazy-rendered
# content has time to land. Order doesn't matter — Playwright races them.
_JD_CONTENT_SELECTORS: tuple[str, ...] = (
    "[data-ph-id*='job-description']",                # Phenom People
    "[data-ph-id*='jobDescription']",
    "[data-automation-id='jobPostingDescription']",   # Workday
    ".job-description",                               # generic
    ".jobDescriptionText",                            # iCIMS / common
    "[itemprop='description']",                       # microdata fallback
    "section[class*='description']",
    "div[class*='description']",
    "article",
)


async def _fetch_rendered_html(
    url: str, *, user_agent: str, timeout_ms: int = 30_000
) -> tuple[str, str]:
    """Load `url` in headless Chromium, return (concatenated rendered HTML, final URL).

    Wait strategy for JS-rendered SPAs (Workday / Phenom / iCIMS / SuccessFactors):
      1. Wait for `domcontentloaded`, then `networkidle` (10 s cap).
      2. Race a list of common JD-content selectors (5 s cap) so we don't
         bail before the description lands.
      3. Scroll to the bottom in steps to trigger lazy loaders.
      4. Poll page content length until it stabilizes (1 s stable / 5 s max).
      5. Concatenate HTML from the main frame AND every child frame, so JDs
         embedded in iframes (some Phenom / Workday flows) are visible to the
         downstream parser.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise IngestError(
            "playwright is not installed. Run `uv sync` then `uv run playwright install chromium`."
        ) from e

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        html = ""
        final_url = url
        try:
            ctx = await browser.new_context(user_agent=user_agent)
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=10_000)

                # Race for any JD content selector — first hit wins.
                selector_union = ", ".join(_JD_CONTENT_SELECTORS)
                with contextlib.suppress(Exception):
                    await page.wait_for_selector(selector_union, timeout=5_000)

                # Scroll-driven lazy loaders: 4 steps, brief pause each.
                with contextlib.suppress(Exception):
                    await page.evaluate(
                        """async () => {
                            const steps = 4;
                            for (let i = 1; i <= steps; i++) {
                                window.scrollTo(0, (document.body.scrollHeight * i) / steps);
                                await new Promise(r => setTimeout(r, 250));
                            }
                            window.scrollTo(0, 0);
                        }"""
                    )

                # Content-length stability poll: bail once length is stable
                # for 1 s, or after 5 s total.
                with contextlib.suppress(Exception):
                    last_len = -1
                    stable_since = 0.0
                    import time as _time
                    start = _time.monotonic()
                    while _time.monotonic() - start < 5.0:
                        cur = await page.content()
                        cur_len = len(cur)
                        if cur_len == last_len:
                            if _time.monotonic() - stable_since >= 1.0:
                                break
                        else:
                            last_len = cur_len
                            stable_since = _time.monotonic()
                        await page.wait_for_timeout(250)

                # Concatenate main frame + every child frame, so iframe-embedded
                # JDs land in the parsed output.
                frames_html: list[str] = []
                main_html = await page.content()
                frames_html.append(main_html)
                for frame in page.frames:
                    if frame is page.main_frame:
                        continue
                    with contextlib.suppress(Exception):
                        frames_html.append(await frame.content())
                html = "\n".join(frames_html)
                final_url = page.url
            finally:
                with contextlib.suppress(Exception):
                    await ctx.close()
        finally:
            with contextlib.suppress(Exception):
                await browser.close()
    return html, final_url


async def fetch_url_as_job(
    url: str,
    *,
    user_agent: str,
    title_override: str | None = None,
    company_override: str | None = None,
) -> Job:
    """Render `url` via Playwright, extract title/company/body, return a Job.

    Strategy:
      1. Prefer a JSON-LD `JobPosting` block — most modern career portals
         (Phenom People, Workday, iCIMS, Greenhouse, Lever, Ashby) embed one
         for SEO. The description there is server-rendered, full-length, and
         immune to JS-hydration timing.
      2. Fall back to the OG-tag + DOM body extractor for older pages.
      3. Raise `IngestError` if the final body is under `MIN_BODY_CHARS` — a
         result that short almost always means the page never rendered, and
         scoring against ~10 words of title text is worse than failing fast.
    """
    html, final_url = await _fetch_rendered_html(url, user_agent=user_agent)

    extracted_title, extracted_company, description, jsonld_location = (
        _parse_html_for_job(html)
    )
    title = title_override or extracted_title
    company = company_override or extracted_company

    if not description:
        raise IngestError(f"no body text extracted from {url}")
    if len(description) < MIN_BODY_CHARS:
        raise IngestError(
            f"extracted JD body too short ({len(description)} chars from {url}) — "
            "page likely didn't render the JD content. Re-run with "
            "`--description-from-stdin` and paste the JD body."
        )

    job_id = _synth_id(url, title, company, description)
    return Job(
        id=job_id,
        source="manual",
        external_id=job_id.split(":", 1)[1],
        company=company,
        title=title,
        location=jsonld_location,
        remote_type="unknown",
        description=description,
        url=final_url,
    )


def _parse_html_for_job(
    html: str,
) -> tuple[str | None, str | None, str, str | None]:
    """Pull title / company / description / location from rendered HTML.
    JSON-LD JobPosting wins when present; otherwise falls back to OG-tag
    metadata + DOM body extraction. Pure function for testability."""
    jsonld = _extract_jsonld_jobposting(html)
    og_title, og_company = _extract_metadata(html)
    title = og_title
    company = og_company
    description = ""
    location: str | None = None

    if jsonld:
        ld_title = jsonld.get("title")
        if isinstance(ld_title, str) and ld_title.strip():
            title = _clean_title(ld_title.strip())
        ld_company = _jobposting_company(jsonld)
        if ld_company:
            company = ld_company
        location = _jobposting_location(jsonld)
        ld_desc = jsonld.get("description")
        if isinstance(ld_desc, str) and ld_desc.strip():
            description = _strip_html(ld_desc)

    if not description:
        description = _extract_body_text(html)

    return title, company, description, location


def build_job_from_text(
    *,
    description: str,
    title: str,
    company: str,
    url: str | None = None,
    location: str | None = None,
) -> Job:
    """Synthesize a manual Job from a pasted JD body. No HTTP fetch involved.
    Used by `apply --url --description-from-stdin` when the renderer can't
    reach a page (logged-in postings, aggressive anti-bot)."""
    description = description.strip()
    if len(description) < MIN_BODY_CHARS:
        raise IngestError(
            f"pasted JD body too short ({len(description)} chars; "
            f"minimum {MIN_BODY_CHARS}). Paste the full JD."
        )
    job_id = _synth_id(url, title, company, description)
    return Job(
        id=job_id,
        source="manual",
        external_id=job_id.split(":", 1)[1],
        company=company,
        title=title,
        location=location,
        remote_type="unknown",
        description=description,
        url=url,
    )


