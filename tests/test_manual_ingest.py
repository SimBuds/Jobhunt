"""Unit tests for `jobhunt.ingest.manual`."""

from __future__ import annotations

import pytest

from jobhunt.errors import IngestError
from jobhunt.ingest.manual import (
    MIN_BODY_CHARS,
    _extract_body_text,
    _extract_jsonld_jobposting,
    _extract_metadata,
    _parse_html_for_job,
    _synth_id,
    build_job_from_text,
)


def test_extract_body_text_strips_chrome() -> None:
    html = """
    <html><head><title>Job</title></head><body>
      <nav>Home About</nav>
      <header>Logo</header>
      <article>
        <h1>Senior Engineer</h1>
        <p>Build cool things with our team.</p>
        <ul><li>Five years of experience</li><li>React</li></ul>
      </article>
      <footer>© 2026</footer>
      <script>tracking()</script>
    </body></html>
    """
    out = _extract_body_text(html)
    assert "Senior Engineer" in out
    assert "Build cool things" in out
    assert "Five years of experience" in out
    assert "Home About" not in out
    assert "Logo" not in out
    assert "© 2026" not in out
    assert "tracking()" not in out


def test_extract_metadata_prefers_og_tags() -> None:
    html = """
    <html><head>
      <title>Senior Engineer at Acme · Lever</title>
      <meta property="og:title" content="Senior Full-Stack Engineer">
      <meta property="og:site_name" content="Acme Robotics">
    </head><body>body</body></html>
    """
    title, company = _extract_metadata(html)
    assert title == "Senior Full-Stack Engineer"
    assert company == "Acme Robotics"


def test_extract_metadata_falls_back_to_title_splitting() -> None:
    html = "<html><head><title>Senior Engineer at Acme · Lever</title></head><body>x</body></html>"
    title, company = _extract_metadata(html)
    assert title == "Senior Engineer"
    assert company == "Acme"


def test_extract_jsonld_jobposting_root_node() -> None:
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "JobPosting",
       "title": "Prompt Engineer",
       "description": "<p>Build prompts.</p><ul><li>LLMs</li></ul>",
       "hiringOrganization": {"@type": "Organization", "name": "Acme AI"},
       "jobLocation": {"@type": "Place",
         "address": {"addressLocality": "Toronto", "addressRegion": "ON"}}}
      </script>
    </head><body>spa shell</body></html>
    """
    node = _extract_jsonld_jobposting(html)
    assert node is not None
    assert node["title"] == "Prompt Engineer"
    assert node["hiringOrganization"]["name"] == "Acme AI"


def test_extract_jsonld_jobposting_in_graph() -> None:
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org",
     "@graph": [
       {"@type": "Organization", "name": "Acme AI"},
       {"@type": "JobPosting", "title": "Engineer", "description": "Build."}
     ]}
    </script>
    """
    node = _extract_jsonld_jobposting(html)
    assert node is not None
    assert node["title"] == "Engineer"


def test_extract_jsonld_jobposting_tolerates_bad_json() -> None:
    """A malformed ld+json block on the page must not block discovery of a
    well-formed JobPosting later in the document."""
    html = """
    <script type="application/ld+json">{this is not json}</script>
    <script type="application/ld+json">
    {"@type": "JobPosting", "title": "Engineer"}
    </script>
    """
    node = _extract_jsonld_jobposting(html)
    assert node is not None
    assert node["title"] == "Engineer"


def test_extract_jsonld_returns_none_when_absent() -> None:
    html = "<html><body>no structured data</body></html>"
    assert _extract_jsonld_jobposting(html) is None


def test_parse_html_for_job_prefers_jsonld() -> None:
    import json as _json
    description_html = (
        "<p>" + "We build prompt-engineering tools and need "
        "an engineer who can refine generative search readiness. " * 6 + "</p>"
    )
    jsonld_payload = _json.dumps({
        "@type": "JobPosting",
        "title": "Prompt Engineer",
        "description": description_html,
        "hiringOrganization": {"name": "Real Co"},
        "jobLocation": {"address": {"addressLocality": "Toronto"}},
    })
    html = f"""
    <html><head>
      <title>Generic OG Title at Generic Co</title>
      <meta property="og:title" content="Generic OG Title">
      <meta property="og:site_name" content="Generic Co">
      <script type="application/ld+json">{jsonld_payload}</script>
    </head><body>spa shell</body></html>
    """
    title, company, description, location = _parse_html_for_job(html)
    assert title == "Prompt Engineer"
    assert company == "Real Co"
    assert "prompt-engineering" in description
    assert location == "Toronto"


def test_parse_html_for_job_falls_back_to_dom() -> None:
    """No JSON-LD → OG-tag metadata + DOM body extraction."""
    body_text = " ".join(["meaningful job copy"] * 50)
    html = f"""
    <html><head>
      <title>Senior Engineer at Acme · Lever</title>
      <meta property="og:title" content="Senior Full-Stack Engineer">
      <meta property="og:site_name" content="Acme Robotics">
    </head><body><article><p>{body_text}</p></article></body></html>
    """
    title, company, description, location = _parse_html_for_job(html)
    assert title == "Senior Full-Stack Engineer"
    assert company == "Acme Robotics"
    assert "meaningful job copy" in description
    assert location is None  # no JSON-LD, no location


def test_build_job_from_text_rejects_short_body() -> None:
    short_body = "Build prompts." * 3
    assert len(short_body) < MIN_BODY_CHARS
    with pytest.raises(IngestError, match="too short"):
        build_job_from_text(
            description=short_body,
            title="Prompt Engineer",
            company="Acme AI",
            url="https://example.com/job/1",
        )


def test_build_job_from_text_accepts_full_body() -> None:
    body = ("We need a prompt engineer to refine generative search "
            "readiness across LLM products. ") * 10
    job = build_job_from_text(
        description=body,
        title="Prompt Engineer",
        company="Acme AI",
        url="https://example.com/job/1",
    )
    assert job.source == "manual"
    assert job.id.startswith("manual:")
    assert job.title == "Prompt Engineer"
    assert job.company == "Acme AI"
    assert job.description.startswith("We need a prompt engineer")


def test_synth_id_is_stable() -> None:
    id_a = _synth_id("https://x.com/1", "Eng", "Acme", "build stuff")
    id_b = _synth_id("https://x.com/1", "Eng", "Acme", "build stuff")
    id_c = _synth_id("https://x.com/2", "Eng", "Acme", "build stuff")
    assert id_a == id_b
    assert id_a != id_c
    assert id_a.startswith("manual:")
    assert len(id_a) == len("manual:") + 12
