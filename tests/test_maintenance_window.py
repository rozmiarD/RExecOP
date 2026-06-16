from __future__ import annotations

from datetime import UTC, datetime

from rexecop.runtime_ops.maintenance import maintenance_window_allows


def test_maintenance_window_allows_inside_configured_window() -> None:
    windows = [{"timezone": "UTC", "start": "00:00", "end": "23:59"}]
    now = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
    allowed, reason = maintenance_window_allows(windows, now=now)
    assert allowed is True
    assert reason.startswith("window_")


def test_maintenance_window_blocks_outside_configured_window() -> None:
    windows = [{"timezone": "UTC", "start": "01:00", "end": "02:00"}]
    now = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
    allowed, reason = maintenance_window_allows(windows, now=now)
    assert allowed is False
    assert reason == "outside_maintenance_window"
