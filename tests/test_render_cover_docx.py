from __future__ import annotations

from pathlib import Path

from docx import Document

from jobhunt.pipeline.cover import CoverLetter
from jobhunt.resume.render_cover_docx import render_cover


def test_render_cover_writes_valid_docx(tmp_path: Path):
    cover = CoverLetter(
        salutation="Dear Hiring Team,",
        body=[
            (
                "I read the posting for the Backend role at Acme and the bit "
                "about Node.js + AWS lined up with what I've been shipping."
            ),
            ("Last year I built a 14+ page Shopify storefront end-to-end as the sole developer."),
        ],
        sign_off="Best,\nCasey Hsu",
        model="qwen3.5:9b",
    )
    out = render_cover(
        cover,
        contact_line="me@example.com | site.com",
        name="Casey Hsu",
        out_path=tmp_path / "cover.docx",
    )
    assert out.exists()
    doc = Document(str(out))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Casey Hsu" in text
    assert "Dear Hiring Team," in text
    assert "Shopify storefront" in text
    assert "Best," in text
