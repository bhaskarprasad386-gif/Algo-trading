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


def test_nifty200_batches_start_on_five_minute_slots(monkeypatch):
    """A 50-symbol batch consumes one five-minute slot before the next starts."""
    symbols = [f"S{i:03d}" for i in range(100)]
    monkeypatch.setattr(scanner, "_nifty200_symbols", lambda: symbols)
    monkeypatch.setattr(scanner, "_nse_market_is_open_now", lambda: False)
    monkeypatch.setattr(scanner, "_scan_one_symbol", lambda ticker, master, historical, end: {
        "status": "success", "matches": []
    })

    class FakeInstrumentMaster:
        pass

    class FakeMarketDataClient:
        pass

    class FakeHistoricalDataClient:
        def __init__(self, *args, **kwargs):
            pass

    from app.market_data import client, historical, instruments
    monkeypatch.setattr(instruments, "InstrumentMaster", FakeInstrumentMaster)
    monkeypatch.setattr(client, "MarketDataClient", FakeMarketDataClient)
    monkeypatch.setattr(historical, "HistoricalDataClient", FakeHistoricalDataClient)
    monkeypatch.setattr(scanner, "_nse_today", lambda: datetime(2026, 10, 23).date())

    monotonic_values = iter([0.0, 120.0, 300.0])
    monkeypatch.setattr(scanner.time, "monotonic", lambda: next(monotonic_values))
    sleeps = []
    monkeypatch.setattr(scanner.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(scanner, "_SCAN_STATE", {})

    scanner._run_nifty200_scan()

    assert scanner._SCAN_STATE["status"] == "completed"
    assert scanner._SCAN_STATE["scanned"] == 100
    assert scanner._SCAN_STATE["batch_number"] == 2
    assert sleeps == [180.0]
