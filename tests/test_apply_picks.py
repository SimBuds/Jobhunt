from __future__ import annotations

from jobhunt.commands.apply_cmd import _parse_picks


def test_parse_picks_csv():
    assert _parse_picks("1,3,7", 10) == [1, 3, 7]


def test_parse_picks_range():
    assert _parse_picks("2-5", 10) == [2, 3, 4, 5]


def test_parse_picks_mixed():
    assert _parse_picks("1, 3-4, 7", 10) == [1, 3, 4, 7]


def test_parse_picks_clips_to_max():
    assert _parse_picks("8-15", 10) == [8, 9, 10]


def test_parse_picks_dedups():
    assert _parse_picks("1,1,2-3,3", 10) == [1, 2, 3]


def test_parse_picks_blank():
    assert _parse_picks("", 10) == []


def test_parse_picks_invalid_chunks_skipped():
    assert _parse_picks("foo,2,bar-baz,4", 10) == [2, 4]
