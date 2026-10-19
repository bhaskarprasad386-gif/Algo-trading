import time
from types import SimpleNamespace
from app.scanner import live_box_spread_routes as routes


def _result(low_ts=None, high_ts=None, symbol="ABC", instrument_class="STOCK"):
    now = time.time_ns()
    low_ts = now if low_ts is None else low_ts
    high_ts = low_ts if high_ts is None else high_ts
    low = SimpleNamespace(underlying=symbol, instrument_class=instrument_class, expiry=20261231,
        strike=100, timestamp_ns=low_ts, call_bid=10, call_ask=11, put_bid=1, put_ask=2,
        lot_size=10, volume=25)
    high = SimpleNamespace(strike=110, timestamp_ns=high_ts, call_bid=8, call_ask=9,
        put_bid=2, put_ask=3, volume=20)
    return SimpleNamespace(low=low, high=high, direction="LONG", executable_edge=6,
        edge_per_lot=60, gross_pnl=60, strike_distance=1)


def test_live_box_route_serializes_bid_ask_snapshot(monkeypatch):
    result = _result()
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    payload = routes.live(10)
    row = payload["data"][0]
    assert row["low_call_bid"] == 10 and row["low_call_ask"] == 11
    assert row["high_put_bid"] == 2 and row["high_put_ask"] == 3
    assert row["low_call_price"] == 11 and row["low_put_price"] == 2
    assert row["high_call_price"] == 8 and row["high_put_price"] == 2
    assert row["liquidity_qty"] == 20
    assert row["timestamp_ns"] == row["high_timestamp_ns"]
    assert row["timestamp_skew_ms"] == 0


def test_live_box_route_preserves_bse_index_identity(monkeypatch):
    result = _result(symbol="SENSEX", instrument_class="INDEX")
    result.low.call_bid, result.low.call_ask = 100, 101
    result.low.put_bid, result.low.put_ask = 110, 111
    result.high.call_bid, result.high.call_ask = 90, 91
    result.high.put_bid, result.high.put_ask = 120, 121
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    row = routes.live(10)["data"][0]
    assert row["symbol"] == "SENSEX"
    assert row["instrument_class"] == "INDEX"
    assert row["low_call_bid"] == 100 and row["low_call_ask"] == 101
    assert row["high_put_bid"] == 120 and row["high_put_ask"] == 121


def test_live_box_route_rejects_stale_high_leg(monkeypatch):
    now = time.time_ns()
    result = _result(low_ts=now, high_ts=now - 10_000_000_000)
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    assert routes.live(10, max_age_seconds=5)["count"] == 0


def test_live_box_route_rejects_future_dated_leg(monkeypatch):
    now = time.time_ns()
    result = _result(low_ts=now, high_ts=now + 1_000_000_000)
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    assert routes.live(10)["count"] == 0


def test_live_box_route_rejects_excessive_timestamp_skew(monkeypatch):
    now = time.time_ns()
    result = _result(low_ts=now, high_ts=now - 4_000_000_000)
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    assert routes.live(10, max_age_seconds=5)["count"] == 0
