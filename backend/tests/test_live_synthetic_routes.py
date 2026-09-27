from types import SimpleNamespace

from app.scanner import live_synthetic_routes


def _result():
    option = SimpleNamespace(
        underlying="NIFTY",
        instrument_class="INDEX",
        expiry=20260930,
        strike=25000.0,
        timestamp_ns=1_000_000_000,
        call_bid=100.0,
        call_ask=101.0,
        put_bid=90.0,
        put_ask=91.0,
    )
    future = SimpleNamespace(
        bid=25100.0,
        ask=25101.0,
        lot_size=65,
    )
    return SimpleNamespace(
        option=option,
        future=future,
        direction="LONG",
        executable_edge=5.0,
        edge_per_lot=325.0,
        gross_pnl=325.0,
        strike_distance=1,
        strike_side="ABOVE",
    )


def test_live_route_serializes_latest_results(monkeypatch):
    result = _result()
    live_synthetic_routes.configure(lambda: (result,))
    response = live_synthetic_routes.live(limit=10)
    assert response["scanner"] == "synthetic-cash-carry-live-1s"
    assert response["opportunity_count"] == 1
    assert response["data"][0]["underlying"] == "NIFTY"
    assert response["data"][0]["executable_edge"] == 5.0


def test_live_route_limits_results():
    result = _result()
    live_synthetic_routes.configure(lambda: (result, result))
    response = live_synthetic_routes.live(limit=1)
    assert response["opportunity_count"] == 1
