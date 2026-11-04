import time
from types import SimpleNamespace

from app.scanner import live_box_spread_websocket as websocket


def _result(now_ns=None):
    now_ns = time.time_ns() if now_ns is None else now_ns
    low = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry=20261231, strike=25000,
        timestamp_ns=now_ns, call_timestamp_ns=now_ns, put_timestamp_ns=now_ns,
        call_ltp=10.5, call_bid=10, call_ask=11, call_bid_qty=101, call_ask_qty=102,
        put_ltp=1.5, put_bid=1, put_ask=2, put_bid_qty=103, put_ask_qty=104, lot_size=25,
    )
    high = SimpleNamespace(
        strike=25100, timestamp_ns=now_ns, call_timestamp_ns=now_ns, put_timestamp_ns=now_ns,
        call_ltp=8.5, call_bid=8, call_ask=9, call_bid_qty=201, call_ask_qty=202,
        put_ltp=2.5, put_bid=2, put_ask=3, put_bid_qty=203, put_ask_qty=204, lot_size=25,
    )
    return SimpleNamespace(
        low=low, high=high, direction="LONG", tradability_status="TRADABLE", executable_edge=6,
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


def test_websocket_row_accepts_four_leg_skew_within_eight_seconds(monkeypatch):
    now_ns = time.time_ns()
    result = _result(now_ns)
    result.low.call_timestamp_ns = now_ns - 4_500_000_000
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)

    row = websocket._row(result)
    assert row is not None
    assert row["timestamp_skew_ms"] == 4500.0


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
    assert row["low_call_timestamp_ns"] == now_ns - 2_000_000_000
    assert row["low_put_timestamp_ns"] == now_ns - 1_800_000_000
    assert row["high_call_timestamp_ns"] == now_ns - 100_000_000
    assert row["high_put_timestamp_ns"] == now_ns - 1_500_000_000
    assert row["freshness"] == "LIVE"



def test_websocket_row_includes_normalized_weekly_expiry_type(monkeypatch):
    now_ns = time.time_ns()
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)
    websocket.set_expiry_types({("NIFTY", "31DEC2026"): "WEEKLY"})

    row = websocket._row(_result(now_ns))

    assert row is not None
    assert row["expiry_type"] == "WEEKLY"


def test_websocket_row_includes_bid_ask_quantities(monkeypatch):
    now_ns = time.time_ns()
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)
    row = websocket._row(_result(now_ns))
    assert row is not None
    assert row["low_call_bid_qty"] == 101 and row["low_call_ask_qty"] == 102
    assert row["low_put_bid_qty"] == 103 and row["low_put_ask_qty"] == 104
    assert row["high_call_bid_qty"] == 201 and row["high_call_ask_qty"] == 202
    assert row["high_put_bid_qty"] == 203 and row["high_put_ask_qty"] == 204

def test_websocket_row_keeps_option_row_when_spot_provider_raises(monkeypatch):
    now_ns = time.time_ns()
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)
    monkeypatch.setattr(websocket, "_spot_provider", lambda _symbol: (_ for _ in ()).throw(RuntimeError("spot feed unavailable")))

    row = websocket._row(_result(now_ns))

    assert row is not None
    assert row["executable_edge"] == 6
    assert row["spot_price"] is None
    assert row["spot_timestamp_ns"] is None


def test_websocket_row_rejects_fractional_leg_timestamp_without_truncating(monkeypatch):
    now_ns = time.time_ns()
    result = _result(now_ns)
    result.low.call_timestamp_ns = str(now_ns) + ".5"
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)
    assert websocket._row(result) is None


def test_websocket_row_rejects_zero_leg_timestamp_without_fallback(monkeypatch):
    now_ns = time.time_ns()
    result = _result(now_ns)
    result.low.call_timestamp_ns = 0
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)
    assert websocket._row(result) is None



def test_publish_emits_empty_frame_when_scan_has_no_qualifying_rows(monkeypatch):
    class FakeLoop:
        def is_running(self):
            return True

        def call_soon_threadsafe(self, callback):
            callback()

    class FakeQueue:
        def __init__(self):
            self.items = []

        def full(self):
            return False

        def put_nowait(self, item):
            self.items.append(item)

    queue = FakeQueue()
    monkeypatch.setattr(websocket, "_loop", FakeLoop())
    monkeypatch.setattr(websocket, "_subscribers", {queue})

    websocket.publish([])

    assert len(queue.items) == 1
    assert queue.items[0]["type"] == "opportunities"
    assert queue.items[0]["data"] == []
