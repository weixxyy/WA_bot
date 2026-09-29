from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from domain import (
    extract_invite_links,
    next_schedule_at,
    parse_schedule_times,
    schedule_slot_key,
    validate_days,
)


def test_extract_invite_links_normalizes_and_deduplicates():
    text = """
    текст https://chat.whatsapp.com/AbC123?source=one,
    https://chat.whatsapp.com/abc123?source=two
    мусор https://example.com/nope
    https://chat.whatsapp.com/XYZ_9
    """

    assert extract_invite_links(text) == [
        "https://chat.whatsapp.com/AbC123",
        "https://chat.whatsapp.com/XYZ_9",
    ]


def test_schedule_parsing_and_next_run():
    assert parse_schedule_times("18:00, 09:00\n18:00") == ["09:00", "18:00"]
    assert validate_days(["4", "0", "4"]) == [0, 4]
    now = datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    assert next_schedule_at(now, [0, 1], ["09:00", "18:00"]) == datetime(
        2026, 9, 28, 18, 0, tzinfo=ZoneInfo("Europe/Moscow")
    )


def test_schedule_slot_key_only_matches_current_minute():
    now = datetime(2026, 9, 28, 9, 0, 45, tzinfo=ZoneInfo("Europe/Moscow"))
    assert schedule_slot_key(now, [0], ["09:00"]).startswith("2026-09-28:09:00")
    assert schedule_slot_key(now, [1], ["09:00"]) is None


@pytest.mark.parametrize("value", ["24:00", "9:00", "09:60", "nope"])
def test_invalid_schedule_time(value):
    with pytest.raises(ValueError):
        parse_schedule_times(value)
