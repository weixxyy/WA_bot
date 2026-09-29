"""Pure domain helpers shared by the web UI, scheduler, and tests."""

import re
from datetime import datetime, timedelta

INVITE_PATTERN = re.compile(
    r"https?://chat\.whatsapp\.com/([A-Za-z0-9_-]+)(?:\?[^\s<>\"']*)?",
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def extract_invite_links(text: str) -> list[str]:
    """Extract normalized, unique WhatsApp group invite URLs from free text."""
    seen = set()
    links = []
    for match in INVITE_PATTERN.finditer(text or ""):
        code = match.group(1)
        key = code.casefold()
        if key in seen:
            continue
        seen.add(key)
        links.append(f"https://chat.whatsapp.com/{code}")
    return links


def parse_schedule_times(text: str) -> list[str]:
    """Parse comma/space/newline-separated HH:MM values."""
    values = []
    for value in re.split(r"[,;\s]+", (text or "").strip()):
        if not value:
            continue
        if not TIME_PATTERN.fullmatch(value):
            raise ValueError(f"Некорректное время {value!r}; нужен формат ЧЧ:ММ")
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("Укажите хотя бы одно время запуска")
    return sorted(values)


def validate_days(days) -> list[int]:
    """Normalize weekdays where Monday is 0 and Sunday is 6."""
    normalized = sorted({int(day) for day in days})
    if not normalized or normalized[0] < 0 or normalized[-1] > 6:
        raise ValueError("Выберите хотя бы один день недели")
    return normalized


def schedule_slot_key(now: datetime, days: list[int], times: list[str]) -> str | None:
    """Return the current minute's stable schedule key when it is a due slot."""
    current = now.strftime("%H:%M")
    if now.weekday() not in days or current not in times:
        return None
    return f"{now.date().isoformat()}:{current}:{getattr(now.tzinfo, 'key', now.tzinfo)}"


def next_schedule_at(now: datetime, days: list[int], times: list[str]) -> datetime:
    """Return the next future scheduled time in the timezone of ``now``."""
    parsed = [(int(value[:2]), int(value[3:])) for value in times]
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        for hour, minute in parsed:
            candidate = datetime(
                day.year,
                day.month,
                day.day,
                hour,
                minute,
                tzinfo=now.tzinfo,
            )
            if candidate > now:
                return candidate
    raise ValueError("Не удалось вычислить следующий запуск")
