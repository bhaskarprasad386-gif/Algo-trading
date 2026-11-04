import time
from types import SimpleNamespace
from app.scanner import live_box_spread_routes as routes


def _result(low_ts=None, high_ts=None, symbol="ABC", instrument_class="STOCK"):
    now = time.time_ns()
    low_ts = now if low_ts is None else low_ts
    high_ts = low_ts if high_ts is None else high_ts
    low = SimpleNamespace(underlying=symbol, instrument_class=instrument_class, expiry=20261231,
        strike=100, timestamp_ns=low_ts, call_ltp=10.5, call_bid=10, call_ask=11, call_bid_qty=101, call_ask_qty=102,
        put_ltp=1.5, put_bid=1, put_ask=2, put_bid_qty=103, put_ask_qty=104, lot_size=10, volume=25)
    high = SimpleNamespace(strike=110, timestamp_ns=high_ts, call_ltp=8.5, call_bid=8,
        call_ask=9, call_bid_qty=201, call_ask_qty=202, put_ltp=2.5, put_bid=2, put_ask=3, put_bid_qty=203, put_ask_qty=204, volume=20)
    return SimpleNamespace(low=low, high=high, direction="LONG", tradability_status="TRADABLE", executable_edge=6,
        edge_per_lot=60, gross_pnl=60, strike_distance=1)


def test_live_box_route_serializes_bid_ask_snapshot(monkeypatch):
    result = _result()
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    payload = routes.live(10)
    row = payload["data"][0]
    assert row["low_call_ltp"] == 10.5 and row["low_call_bid"] == 10 and row["low_call_ask"] == 11
    assert row["low_put_ltp"] == 1.5 and row["low_put_bid"] == 1 and row["low_put_ask"] == 2
    assert row["low_call_bid_qty"] == 101 and row["low_call_ask_qty"] == 102
    assert row["low_put_bid_qty"] == 103 and row["low_put_ask_qty"] == 104
    assert row["high_call_bid_qty"] == 201 and row["high_call_ask_qty"] == 202
    assert row["high_put_bid_qty"] == 203 and row["high_put_ask_qty"] == 204
    assert row["high_call_ltp"] == 8.5 and row["high_call_bid"] == 8 and row["high_call_ask"] == 9
    assert row["high_put_ltp"] == 2.5 and row["high_put_bid"] == 2 and row["high_put_ask"] == 3
    assert row["low_call_price"] == 11 and row["low_put_price"] == 2
    assert row["high_call_price"] == 8 and row["high_put_price"] == 2
    assert row["liquidity_qty"] == 20
    assert row["timestamp_ns"] == row["high_timestamp_ns"]
    assert row["last_update_timestamp_ns"] == row["timestamp_ns"]
    assert row["expiry_date"] == "2026-12-31"
    assert row["expiry_type"] == "UNKNOWN"
    assert row["freshness"] == "LIVE"
    assert row["quote_age_seconds"] >= 0
    assert row["timestamp_skew_ms"] == 0
    assert row["low_call_timestamp_ns"] == result.low.timestamp_ns
    assert row["low_put_timestamp_ns"] == result.low.timestamp_ns
    assert row["high_call_timestamp_ns"] == result.high.timestamp_ns
    assert row["high_put_timestamp_ns"] == result.high.timestamp_ns


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


def test_live_box_route_accepts_four_leg_skew_within_eight_seconds_when_each_leg_is_fresh(monkeypatch):
    now = time.time_ns()
    result = _result(low_ts=now, high_ts=now - 4_500_000_000)
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    assert routes.live(10, max_age_seconds=5)["count"] == 1


def test_live_box_route_normalizes_expiry_type_keys(monkeypatch):
    result = _result(symbol="NIFTY", instrument_class="INDEX")
    result.low.expiry = "27 Oct 2026"
    result.high.expiry = "27 Oct 2026"
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    monkeypatch.setattr(routes, "_expiry_types", {})
    routes.set_expiry_types({("NIFTY", "27OCT2026"): "WEEKLY"})
    row = routes.live(10)["data"][0]
    assert row["expiry_date"] == "2026-10-27"
    assert row["expiry_type"] == "WEEKLY"
    assert row["freshness"] == "LIVE"



def test_live_box_route_accepts_skew_hidden_inside_strike_timestamps_when_each_leg_is_fresh(monkeypatch):
    now = time.time_ns()
    result = _result(low_ts=now - 1_000_000_000, high_ts=now - 1_000_000_000)
    result.low.call_timestamp_ns = now - 4_500_000_000
    result.low.put_timestamp_ns = now - 1_000_000_000
    result.high.call_timestamp_ns = now - 1_000_000_000
    result.high.put_timestamp_ns = now - 1_000_000_000
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    assert routes.live(10, max_age_seconds=5)["count"] == 1


def test_live_box_route_age_uses_oldest_individual_option_quote(monkeypatch):
    now = time.time_ns()
    result = _result(low_ts=now - 2_000_000_000, high_ts=now - 1_500_000_000)
    result.low.call_timestamp_ns = now - 1_900_000_000
    result.low.put_timestamp_ns = now - 1_800_000_000
    result.high.call_timestamp_ns = now - 100_000_000
    result.high.put_timestamp_ns = now - 1_500_000_000
    monkeypatch.setattr(routes, "_latest", lambda: (result,))

    row = routes.live(10, max_age_seconds=5)["data"][0]

    assert row["last_update_timestamp_ns"] == now - 100_000_000
    assert 1.8 <= row["quote_age_seconds"] < 2.1


def test_live_box_route_reports_actual_four_leg_skew(monkeypatch):
    now = time.time_ns()
    result = _result(low_ts=now - 500_000_000, high_ts=now)
    result.low.call_timestamp_ns = now - 1_000_000_000
    result.low.put_timestamp_ns = now - 800_000_000
    result.high.call_timestamp_ns = now - 400_000_000
    result.high.put_timestamp_ns = now
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    row = routes.live(10, max_age_seconds=5)["data"][0]
    assert row["timestamp_skew_ms"] == 1000.0



def test_box_spread_diagnostics_endpoint_fails_closed_without_runner(monkeypatch):
    monkeypatch.setattr(routes, "_diagnostics_getter", None)
    payload = routes.diagnostics()
    assert payload["strategy"] == "box-spread"
    assert payload["status"] == "waiting"
    assert payload["counts"] == {}
    assert payload["active_buckets"] == 0


def test_box_spread_diagnostics_endpoint_returns_scanner_snapshot(monkeypatch):
    snapshot = {
        "counts": {"missing_bid_quote": 2},
        "last_scan": {"outcome": "waiting_for_complete_executable_call_put_pairs"},
        "active_buckets": 1,
    }
    monkeypatch.setattr(routes, "_diagnostics_getter", lambda: snapshot)
    payload = routes.diagnostics()
    assert payload["status"] == "available"
    assert payload["counts"]["missing_bid_quote"] == 2
    assert payload["last_scan"]["outcome"] == "waiting_for_complete_executable_call_put_pairs"



def test_alert_history_includes_saved_expiry_type(monkeypatch):
    row = SimpleNamespace(
        symbol="NIFTY", instrument_class="INDEX", expiry=20261123,
        expiry_type="WEEKLY", low_strike=22000, high_strike=22100,
        direction="LONG", timestamp_ns=123,
        low_call_bid=1, low_call_bid_qty=None, low_call_ask=2, low_call_ask_qty=None,
        low_put_bid=3, low_put_bid_qty=None, low_put_ask=4, low_put_ask_qty=None,
        high_call_bid=5, high_call_bid_qty=None, high_call_ask=6, high_call_ask_qty=None,
        high_put_bid=7, high_put_bid_qty=None, high_put_ask=8, high_put_ask_qty=None,
        executable_edge=1, edge_per_lot=10, gross_pnl=10, lot_size=25,
        depth_edge_per_unit=10.0, max_executable_lots=2, tradability_status="TRADABLE",
    )

    class Query:
        def filter(self, *args): return self
        def order_by(self, *args): return self
        def limit(self, *args): return self
        def yield_per(self, *args): return self
        def __iter__(self): return iter([row])
        def all(self): return [row]

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def query(self, *args): return Query()

    monkeypatch.setattr(routes, "SessionLocal", Session)
    payload = routes.alerts(days=30, limit=500)
    assert payload["count"] == 1
    assert payload["data"][0]["expiry_type"] == "WEEKLY"
    assert payload["data"][0]["expiry_date"] == "2026-11-23"


def test_alert_history_falls_back_to_current_instrument_master_classification(monkeypatch):
    row = SimpleNamespace(
        symbol="NIFTY", instrument_class="INDEX", expiry=20261123,
        expiry_type=None, low_strike=22000, high_strike=22100,
        direction="LONG", timestamp_ns=123,
        low_call_bid=1, low_call_bid_qty=None, low_call_ask=2, low_call_ask_qty=None,
        low_put_bid=3, low_put_bid_qty=None, low_put_ask=4, low_put_ask_qty=None,
        high_call_bid=5, high_call_bid_qty=None, high_call_ask=6, high_call_ask_qty=None,
        high_put_bid=7, high_put_bid_qty=None, high_put_ask=8, high_put_ask_qty=None,
        executable_edge=1, edge_per_lot=10, gross_pnl=10, lot_size=25,
        depth_edge_per_unit=5.0, max_executable_lots=2, tradability_status="TRADABLE",
    )

    class Query:
        def filter(self, *args): return self
        def order_by(self, *args): return self
        def limit(self, *args): return self
        def all(self): return [row]

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def query(self, *args): return Query()

    monkeypatch.setattr(routes, "SessionLocal", Session)
    monkeypatch.setattr(routes, "_expiry_types", {("NIFTY", routes._expiry_key(20261123)): "MONTHLY"})
    payload = routes.alerts(days=30, limit=500)
    assert payload["data"][0]["expiry_type"] == "MONTHLY"


def test_snapshot_history_prefers_saved_expiry_type_over_changed_master(monkeypatch):
    from datetime import datetime, timezone
    import json as json_module

    row = SimpleNamespace(
        id=1, symbol="BANKNIFTY", expiry=20261123,
        observed_at=datetime(2026, 10, 29, tzinfo=timezone.utc).replace(tzinfo=None),
        payload_json=json_module.dumps({"expiry_type": "WEEKLY", "symbol": "BANKNIFTY", "tradability_status": "TRADABLE", "max_executable_lots": 2, "depth_edge_per_unit": 10.0}),
    )

    class Query:
        def filter(self, *args): return self
        def order_by(self, *args): return self
        def offset(self, *args): return self
        def limit(self, *args): return self
        def yield_per(self, *args): return self
        def __iter__(self): return iter([row])
        def count(self): return 1
        def all(self): return [row]

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def query(self, *args): return Query()

    monkeypatch.setattr(routes, "SessionLocal", Session)
    monkeypatch.setattr(
        routes, "_expiry_types",
        {("BANKNIFTY", routes._expiry_key(20261123)): "MONTHLY"},
    )
    payload = routes.snapshots(days=30, limit=500, offset=0, expiry_type=None)
    assert payload["data"][0]["expiry_type"] == "WEEKLY"


def test_snapshot_history_uses_current_classification_only_when_saved_type_missing(monkeypatch):
    from datetime import datetime, timezone
    import json as json_module

    row = SimpleNamespace(
        id=1, symbol="BANKNIFTY", expiry=20261123,
        observed_at=datetime(2026, 10, 29, tzinfo=timezone.utc).replace(tzinfo=None),
        payload_json=json_module.dumps({"symbol": "BANKNIFTY", "tradability_status": "TRADABLE", "max_executable_lots": 2, "depth_edge_per_unit": 10.0}),
    )

    class Query:
        def filter(self, *args): return self
        def order_by(self, *args): return self
        def offset(self, *args): return self
        def limit(self, *args): return self
        def yield_per(self, *args): return self
        def __iter__(self): return iter([row])
        def count(self): return 1
        def all(self): return [row]

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def query(self, *args): return Query()

    monkeypatch.setattr(routes, "SessionLocal", Session)
    monkeypatch.setattr(
        routes, "_expiry_types",
        {("BANKNIFTY", routes._expiry_key(20261123)): "MONTHLY"},
    )
    payload = routes.snapshots(days=30, limit=500, offset=0, expiry_type=None)
    assert payload["data"][0]["expiry_type"] == "MONTHLY"



def test_raw_monitor_route_returns_fresh_edge_even_when_normal_entry_threshold_would_hide_it(monkeypatch):
    result = _result()
    result.executable_edge = 0.5
    now_ns = max(result.low.timestamp_ns, result.high.timestamp_ns)
    monkeypatch.setattr(routes, "_monitor_getter", lambda: {"updated_at_ns": now_ns, "data": (result,), "entry_data": (result,)})
    payload = routes.monitor_snapshot(max_age_seconds=5)
    assert payload["fresh"] is True
    assert payload["count"] == 1
    assert payload["data"][0]["executable_edge"] == 0.5
    assert payload["data"][0]["freshness"] == "LIVE_MONITOR"
    assert payload["entry_count"] == 1
    assert payload["entry_data"][0]["freshness"] == "LIVE_RULE_CANDIDATE"
    assert payload["data"][0]["low_call_bid"] == 10
    assert payload["data"][0]["low_call_timestamp_ns"] == result.low.timestamp_ns
    assert payload["data"][0]["high_put_timestamp_ns"] == result.high.timestamp_ns
    assert payload["data"][0]["timestamp_skew_ms"] == 0
    assert payload["data"][0]["high_put_ask"] == 3


def test_raw_monitor_route_fails_closed_for_stale_snapshot(monkeypatch):
    result = _result()
    monkeypatch.setattr(
        routes, "_monitor_getter",
        lambda: {"updated_at_ns": time.time_ns() - 10_000_000_000, "data": (result,)},
    )
    payload = routes.monitor_snapshot(max_age_seconds=5)
    assert payload["fresh"] is False
    assert payload["count"] == 0
    assert payload["data"] == []

def test_live_box_route_keeps_option_row_when_spot_provider_raises(monkeypatch):
    result = _result()
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    monkeypatch.setattr(routes, "_spot_provider", lambda _symbol: (_ for _ in ()).throw(RuntimeError("spot feed unavailable")))

    payload = routes.live(10)

    assert payload["count"] == 1
    row = payload["data"][0]
    assert row["executable_edge"] == 6
    assert row["spot_price"] is None
    assert row["spot_timestamp_ns"] is None
    assert row["spot_age_seconds"] is None


def test_live_route_rejects_fractional_leg_timestamp_without_truncating(monkeypatch):
    result = _result()
    result.low.call_timestamp_ns = str(result.low.timestamp_ns) + ".5"
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    assert routes.live(10)["count"] == 0


def test_live_route_rejects_zero_leg_timestamp_without_fallback(monkeypatch):
    result = _result()
    result.low.call_timestamp_ns = 0
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    assert routes.live(10)["count"] == 0


def test_monitor_route_reports_all_four_leg_timestamps_and_exact_skew(monkeypatch):
    now = time.time_ns()
    result = _result(low_ts=now - 500_000_000, high_ts=now)
    result.low.call_timestamp_ns = now - 1_000_000_000
    result.low.put_timestamp_ns = now - 800_000_000
    result.high.call_timestamp_ns = now - 400_000_000
    result.high.put_timestamp_ns = now
    monkeypatch.setattr(
        routes, "_monitor_getter",
        lambda: {"updated_at_ns": now, "data": (result,), "entry_data": ()},
    )

    row = routes.monitor_snapshot(max_age_seconds=5)["data"][0]

    assert row["low_call_timestamp_ns"] == now - 1_000_000_000
    assert row["low_put_timestamp_ns"] == now - 800_000_000
    assert row["high_call_timestamp_ns"] == now - 400_000_000
    assert row["high_put_timestamp_ns"] == now
    assert row["timestamp_skew_ms"] == 1000.0


def test_monitor_route_rejects_zero_leg_timestamp_without_fallback(monkeypatch):
    result = _result()
    result.low.call_timestamp_ns = 0
    monkeypatch.setattr(
        routes, "_monitor_getter",
        lambda: {"updated_at_ns": result.low.timestamp_ns, "data": (result,), "entry_data": ()},
    )
    payload = routes.monitor_snapshot(max_age_seconds=5)
    assert payload["fresh"] is True
    assert payload["count"] == 0


def test_http_and_websocket_box_rows_keep_identical_edge_and_executable_prices(monkeypatch):
    from app.scanner import live_box_spread_websocket as websocket

    result = _result()
    now_ns = max(result.low.timestamp_ns, result.high.timestamp_ns)
    result.low.call_timestamp_ns = now_ns
    result.low.put_timestamp_ns = now_ns
    result.high.call_timestamp_ns = now_ns
    result.high.put_timestamp_ns = now_ns
    monkeypatch.setattr(routes, "_latest", lambda: (result,))
    monkeypatch.setattr(routes.time, "time_ns", lambda: now_ns)
    monkeypatch.setattr(websocket.time, "time_ns", lambda: now_ns)

    http_row = routes.live(10)["data"][0]
    ws_row = websocket._row(result)

    for key in (
        "executable_edge", "edge_per_lot", "gross_pnl", "low_call_price",
        "low_put_price", "high_call_price", "high_put_price",
        "last_update_timestamp_ns", "quote_age_seconds", "timestamp_skew_ms",
    ):
        assert http_row[key] == ws_row[key]


def test_index_spot_snapshot_reads_only_manually_selected_indices(monkeypatch):
    seen = []
    monkeypatch.setattr(routes, "_index_selection_provider", lambda: ("SENSEX", "NIFTY"))
    monkeypatch.setattr(
        routes,
        "_spot_fields",
        lambda symbol: seen.append(symbol) or {"spot_price": None, "spot_timestamp_ns": None, "spot_age_seconds": None},
    )

    payload = routes.index_spot_snapshot()

    assert [row["symbol"] for row in payload["data"]] == ["NIFTY", "SENSEX"]
    assert seen == ["NIFTY", "SENSEX"]


def test_index_spot_snapshot_fails_closed_when_selection_provider_errors(monkeypatch):
    seen = []
    def fail_selection():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(routes, "_index_selection_provider", fail_selection)
    monkeypatch.setattr(
        routes,
        "_spot_fields",
        lambda symbol: seen.append(symbol) or {"spot_price": None, "spot_timestamp_ns": None, "spot_age_seconds": None},
    )

    payload = routes.index_spot_snapshot()

    assert payload["data"] == []
    assert seen == []
