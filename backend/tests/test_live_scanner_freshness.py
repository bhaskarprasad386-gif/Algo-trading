import time

from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner




def test_box_rejects_stale_quote_at_ingress(monkeypatch):
    now = 1_800_000_000_000_000_000
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
