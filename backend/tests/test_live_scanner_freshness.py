import time

from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.scanner.live_calendar_spread_scanner import LiveCalendarSpreadScanner
from app.scanner.live_synthetic_scanner import LiveSyntheticScanner
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner


def test_calendar_rejects_stale_quote_before_alert_or_signal(monkeypatch):
    now = 10_000_000_000
    monkeypatch.setattr(time, "time_ns", lambda: now)
    record = MarketDataRecord(
        instrument=InstrumentKey("NFO", "NFO", "1"),
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURE,
        timestamp_ns=now - 5_000_000_001,
        bid=100.0,
        ask=101.0,
        bid_qty=10,
        ask_qty=10,
        expiry="01JAN2030",
        lot_size=50,
    )
    scanner = LiveCalendarSpreadScanner(minimum_gap_points=0)
    assert scanner.update(record) is None
    assert scanner.snapshot() == ()


def test_synthetic_rejects_stale_quote_at_ingress(monkeypatch):
    now = 10_000_000_000
    monkeypatch.setattr(time, "time_ns", lambda: now)
    scanner = LiveSyntheticScanner(atm_provider=lambda _symbol, _ts: 100.0)
    payload = {
        "underlying": "NIFTY",
        "instrument_class": "INDEX",
        "option_type": "CE",
        "strike": 100.0,
        "source_timestamp_ns": now - 5_000_000_001,
        "bid": 10.0,
        "ask": 11.0,
    }
    assert scanner.observe(payload) == ()


def test_box_rejects_stale_quote_at_ingress(monkeypatch):
    now = 10_000_000_000
    monkeypatch.setattr(time, "time_ns", lambda: now)
    scanner = LiveBoxSpreadScanner(atm_provider=lambda _symbol, _ts: 100.0)
    payload = {
        "underlying": "NIFTY",
        "instrument_class": "INDEX",
        "option_type": "CE",
        "strike": 100.0,
        "expiry": "01JAN2030",
        "source_timestamp_ns": now - 5_000_000_001,
        "bid": 10.0,
        "ask": 11.0,
    }
    assert scanner.observe(payload) == ()
