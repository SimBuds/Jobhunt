"""Strip a stray trailing sign-off line from cover-letter body paragraphs.

qwen3.5:9b habitually appends 'Best,' or 'Best,\\nCasey Hsu' to the last body
paragraph despite the schema's sign_off field being rendered separately. The
deterministic strip prevents the duplicate-sign-off violation from firing in
the validator.
"""

from __future__ import annotations

from jobhunt.pipeline.cover import _strip_trailing_signoff


def test_strips_best_with_name() -> None:
    para = "I am ready to discuss this role. Best,\nCasey Hsu"
    assert _strip_trailing_signoff(para) == "I am ready to discuss this role."


def test_strips_best_alone() -> None:
    para = "Looking forward to hearing from you. Best,"
    assert _strip_trailing_signoff(para) == "Looking forward to hearing from you."


def test_strips_regards_kind_sincerely() -> None:
    for closer in ("Regards,", "Sincerely,", "Cheers,", "Kind regards,"):
        para = f"This closes the para. {closer}"
        assert _strip_trailing_signoff(para) == "This closes the para."


def test_strips_signoff_on_new_line() -> None:
    para = "This closes the para.\n\nBest,\nCasey Hsu"
    assert _strip_trailing_signoff(para) == "This closes the para."


def test_passthrough_when_no_signoff() -> None:
    para = "I look forward to discussing this role with your team."
    assert _strip_trailing_signoff(para) == para


def test_does_not_strip_inline_best() -> None:
    # "best" used as an adjective mid-sentence must not be touched.
    para = "I do my best work on greenfield e-commerce builds."
    assert _strip_trailing_signoff(para) == para
