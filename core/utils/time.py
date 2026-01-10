from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def moscow_tz() -> ZoneInfo:
    return ZoneInfo("Europe/Moscow")


def ensure_tzaware(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def floor_to_day_close(dt: datetime, tz: ZoneInfo) -> datetime:
    """
    Anchor a signal to a day-close timestamp (23:59:59 local time) for idempotency.

    We use 23:59:59 instead of 00:00:00 to avoid ambiguity between "day opened" and "day closed"
    and to align with end-of-day decision-making.
    """
    dt_local = ensure_tzaware(dt, tz)
    d: date = dt_local.date()
    return datetime.combine(d, time(23, 59, 59), tzinfo=tz)


def iter_days(start: date, end: date) -> list[date]:
    if end < start:
        return []
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur = cur + timedelta(days=1)
    return days

