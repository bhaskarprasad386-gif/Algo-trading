import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.scanner import weekly_swing_scanner as scanner


def test_daily_scheduler_skips_holiday_then_starts_on_next_trading_day(monkeypatch):
    """A holiday wake-up must not start a scan; the next trading day may."""
    ist = ZoneInfo("Asia/Kolkata")
    now_values = iter([
        datetime(2026, 10, 20, 15, 39, tzinfo=ist),  # Dussehra holiday
        datetime(2026, 10, 20, 15, 40, tzinfo=ist),  # holiday wake-up: skip
        datetime(2026, 10, 21, 15, 40, tzinfo=ist),  # next trading-day wake-up
        datetime(2026, 10, 21, 15, 40, tzinfo=ist),
        datetime(2026, 10, 21, 15, 40, tzinfo=ist),
    ])

    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            value = next(now_values)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(scanner, "datetime", FixedDateTime)
    monkeypatch.setattr(scanner, "_SCAN_STATE", {"status": "idle"})

    started = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            started.append(kwargs.get("name"))

        def start(self):
            started.append("started")

    monkeypatch.setattr(scanner.threading, "Thread", FakeThread)

    sleep_calls = []
    sleep_count = 0

    async def fake_sleep(seconds):
        nonlocal sleep_count
        sleep_count += 1
        sleep_calls.append(seconds)
        if sleep_count == 3:
            raise RuntimeError("stop scheduler test")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop scheduler test"):
        asyncio.run(scanner.weekly_swing_daily_refresh_loop())

    # The holiday wake is skipped. The following trading-day wake creates and
    # starts exactly one scan thread before the test stops the loop.
    assert len(sleep_calls) == 3
    assert started == ["weekly-nifty200-daily", "started"]
    assert scanner._SCAN_STATE["trigger"] == "daily_off_market_schedule"


def test_daily_scheduler_does_not_start_when_scan_is_already_running(monkeypatch):
    """The daily trigger must not create a second concurrent scan."""
    ist = ZoneInfo("Asia/Kolkata")

    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 21, 15, 40, tzinfo=ist)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(scanner, "datetime", FixedDateTime)
    monkeypatch.setattr(scanner, "_SCAN_STATE", {"status": "running"})

    started = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            started.append("created")

        def start(self):
            started.append("started")

    monkeypatch.setattr(scanner.threading, "Thread", FakeThread)

    sleep_count = 0

    async def fake_sleep(seconds):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 2:
            raise RuntimeError("stop scheduler test")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop scheduler test"):
        asyncio.run(scanner.weekly_swing_daily_refresh_loop())

    assert sleep_count == 2
    assert started == []
