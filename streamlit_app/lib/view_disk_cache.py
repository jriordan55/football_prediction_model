"""Shared disk-cache helpers for instant view loads + explicit refresh."""
from __future__ import annotations

from datetime import datetime, timezone


def format_cache_age(ts: datetime | None) -> str:
    if ts is None:
        return "not cached yet"
    age_min = max(0, int((datetime.now(timezone.utc) - ts).total_seconds() // 60))
    if age_min < 1:
        return "just now"
    if age_min < 60:
        return f"{age_min} min ago"
    hours = age_min // 60
    return f"{hours}h ago" if hours < 48 else ts.astimezone().strftime("%b %d · %I:%M %p")


def parse_cache_timestamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (TypeError, ValueError):
        return None
