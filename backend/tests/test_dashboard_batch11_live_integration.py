from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "backend" / "app" / "main.py"


def test_dashboard_push_contains_only_retained_box_spread_snapshot():
    src = MAIN.read_text(encoding="utf-8")
    start = src.index("def _build_dashboard_snapshot_payload")
    end = src.index("def _build_dashboard_snapshot_frame", start)
    payload = src[start:end]
    assert '"box_spread":' in payload
    assert '"cash_future":' not in payload
    assert '"calendar_spread":' not in payload
    assert '"synthetic_arbitrage":' not in payload
    assert "live_cash_future_scanner.snapshot" not in payload
    assert "live_calendar_spread_scanner.snapshot" not in payload


def test_dashboard_push_never_opens_a_broker_socket_per_browser():
    src = MAIN.read_text(encoding="utf-8")
    assert '"broker_socket_per_browser": False' in src
    assert "SharedIndexMarketDataRunner (NIFTY/BANKNIFTY)" in src
    assert "app.include_router(live_box_spread_websocket_router)" in src
