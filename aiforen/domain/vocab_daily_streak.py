"""Consecutive calendar-day streak from per-day activity counts."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Mapping, Optional


def parse_day_key(key: str) -> Optional[date]:
    text = str(key or "").strip()[:10]
    if len(text) < 10:
        return None
    try:
        year, month, day = text.split("-")
        return date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def compute_daily_streak(
    daily_counts: Mapping[str, int],
    *,
    today: date,
) -> int:
    """
    Count consecutive days with activity > 0 ending on today, or yesterday if
    the learner has not studied yet today (streak still "alive" until day ends).
    """
    active: set[date] = set()
    for key, count in daily_counts.items():
        if int(count or 0) <= 0:
            continue
        day = parse_day_key(str(key))
        if day:
            active.add(day)

    if not active:
        return 0

    yesterday = today - timedelta(days=1)
    if today in active:
        anchor = today
    elif yesterday in active:
        anchor = yesterday
    else:
        return 0

    streak = 0
    cursor = anchor
    while cursor in active:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def vocab_counts_from_daily_activity(
    daily_activity: Mapping[str, object]
) -> dict[str, int]:
    """Build YYYY-MM-DD -> vocab attempts from user_stats.daily_activity."""
    counts: dict[str, int] = {}
    for day, payload in daily_activity.items():
        day_key = str(day).strip()[:10]
        if len(day_key) < 10:
            continue
        bump = 0
        if isinstance(payload, dict):
            bump = int(payload.get("vocab") or 0)
        elif isinstance(payload, (int, float)):
            bump = int(payload)
        if bump > 0:
            counts[day_key] = max(counts.get(day_key, 0), bump)
    return counts
