from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def maintenance_window_allows(
    windows: list[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Return whether apply is allowed under configured maintenance windows."""
    if not windows:
        return True, "no_maintenance_windows_configured"
    current = now or datetime.now(UTC)
    for index, window in enumerate(windows):
        timezone_name = str(window.get("timezone") or "UTC")
        start_raw = str(window.get("start") or "")
        end_raw = str(window.get("end") or "")
        if not start_raw or not end_raw:
            continue
        tz = ZoneInfo(timezone_name)
        local_now = current.astimezone(tz)
        start = _parse_hhmm(start_raw)
        end = _parse_hhmm(end_raw)
        local_time = local_now.time()
        if start <= end:
            if start <= local_time <= end:
                return True, f"window_{index}_open"
        elif local_time >= start or local_time <= end:
            return True, f"window_{index}_open"
    return False, "outside_maintenance_window"
