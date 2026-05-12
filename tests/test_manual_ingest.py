"""Unit tests for `jobhunt.ingest.manual`."""

from __future__ import annotations

from jobhunt.ingest.manual import (
    _extract_body_text,
    _extract_metadata,
    _synth_id,
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


def test_synth_id_is_stable() -> None:
    id_a = _synth_id("https://x.com/1", "Eng", "Acme", "build stuff")
    id_b = _synth_id("https://x.com/1", "Eng", "Acme", "build stuff")
    id_c = _synth_id("https://x.com/2", "Eng", "Acme", "build stuff")
    assert id_a == id_b
    assert id_a != id_c
    assert id_a.startswith("manual:")
    assert len(id_a) == len("manual:") + 12
