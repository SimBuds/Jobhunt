from __future__ import annotations

import pytest

from jobhunt.ingest._filter import is_research_title


@pytest.mark.parametrize(
    "title,expected",
    [
        # Hits — the noise we want to stop scoring.
        ("Senior Applied AI/ML Scientist - Listing Quality", True),
        ("Applied Scientist", True),
        ("ML Scientist", True),
        ("AI Scientist", True),
        ("Machine Learning Scientist", True),
        ("Senior Research Engineer, Foundation Models", True),
        ("Research Scientist", True),
        ("Data Scientist", True),
        ("Senior Data Engineer", True),
        ("Staff Data Engineer - Platform Data and Analytics", True),
        ("Data Platform Engineer", True),
        ("Quantitative Researcher", True),
        ("Quant Developer", True),
        # Misses — legitimate IC roles that must pass through.
        # July 2026: AI/ML Engineer titles now pass — they increasingly mean
        # LLM-integration full-stack work (Casey's AI lane); the scorer
        # handles the genuinely research-flavored ones.
        ("Machine Learning Engineer", False),
        ("ML Engineer", False),
        ("AI Engineer", False),
        ("Senior AI Engineer, Developer Tooling", False),
        ("Senior Software Engineer", False),
        ("Staff Engineer - Growth Platform", False),
        ("Frontend Engineer", False),
        ("Full-Stack Developer", False),
        ("Shopify Developer", False),
        ("Senior Frontend or Backend Engineer - Growth", False),
        ("Engineering Lead, Web", False),
        # Empty / None defensive cases.
        ("", False),
        (None, False),
    ],
)
def test_is_research_title(title: str | None, expected: bool) -> None:
    assert is_research_title(title) is expected
