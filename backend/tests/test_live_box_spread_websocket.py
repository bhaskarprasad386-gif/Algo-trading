import time
from types import SimpleNamespace

from app.scanner import live_box_spread_websocket as websocket


def _result(now_ns=None):
    now_ns = time.time_ns() if now_ns is None else now_ns
    low = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry=20261231, strike=25000,
        timestamp_ns=now_ns, call_timestamp_ns=now_ns, put_timestamp_ns=now_ns,
        call_ltp=10.5, call_bid=10, call_ask=11,
        put_ltp=1.5, put_bid=1, put_ask=2, lot_size=25,
    )
    high = SimpleNamespace(
        strike=25100, timestamp_ns=now_ns, call_timestamp_ns=now_ns, put_timestamp_ns=now_ns,
        call_ltp=8.5, call_bid=8, call_ask=9,
        put_ltp=2.5, put_bid=2, put_ask=3, lot_size=25,
    )
    return SimpleNamespace(
        low=low, high=high, direction="LONG", executable_edge=6,
        edge_per_lot=60, gross_pnl=60, strike_distance=100,
    )


def test_websocket_row_rejects_stale_individual_leg(monkeypatch):
    now_ns = time.time_ns()
    result = _result(now_ns)
    result.low.call_timestamp_ns = now_ns - 6_000_000_000
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)

    assert websocket._row(result) is None


def test_websocket_row_rejects_future_dated_leg(monkeypatch):
    now_ns = time.time_ns()
    result = _result(now_ns)
    result.high.put_timestamp_ns = now_ns + 1_000_000
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)

    assert websocket._row(result) is None


def test_websocket_row_rejects_excessive_four_leg_skew(monkeypatch):
    now_ns = time.time_ns()
    result = _result(now_ns)
    result.low.call_timestamp_ns = now_ns - 3_500_000_000
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)

    assert websocket._row(result) is None


def test_websocket_quote_age_uses_oldest_leg_and_reports_newest(monkeypatch):
    now_ns = time.time_ns()
    result = _result(now_ns)
    result.low.call_timestamp_ns = now_ns - 2_000_000_000
    result.low.put_timestamp_ns = now_ns - 1_800_000_000
    result.high.call_timestamp_ns = now_ns - 100_000_000
    result.high.put_timestamp_ns = now_ns - 1_500_000_000
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)

    row = websocket._row(result)

    assert row is not None
    assert row["last_update_timestamp_ns"] == now_ns - 100_000_000
    assert 1.9 <= row["quote_age_seconds"] <= 2.1
    assert row["timestamp_skew_ms"] == 1900.0
    assert row["freshness"] == "LIVE"
