import json
from pathlib import Path

ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
API_SERVICE = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_cash_future_response_fixture_matches_displayed_fields():
    fixture = {"status": "ok", "scanner": "cash-future", "mode": "automatic", "symbols_requested": ["ABC"], "scanned_observations": 1, "opportunity_count": 1, "data": [{"symbol": "ABC", "cash_price": 100.0, "future_price": 105.0, "gap": 5.0, "gap_pct": 5.0, "gross_spread_profit": 500.0, "margin_required": 1000.0, "deployed_capital": 1500.0, "net_profit": 450.0, "roi_pct": 30.0, "executable": True}], "errors": []}
    parsed = json.loads(json.dumps(fixture))
    opportunity = parsed["data"][0]
    api = API_SERVICE.read_text(encoding="utf-8")
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    for field in opportunity:
        assert f"val {field}:" in api
    for field in ("cash_ask", "future_bid", "gap", "gap_pct", "net_gap_pct", "net_profit", "cash_day_high", "cash_day_low", "future_day_high", "future_day_low", "liquidity_qty", "stable_observations", "lifecycle", "alert_event"):
        assert f"item.{field}" in ui
    assert 'item.lifecycle' in ui


def test_android_cash_future_error_fixture_is_supported():
    fixture = {"status": "ok", "scanner": "cash-future", "mode": "automatic", "symbols_requested": [], "scanned_observations": 0, "opportunity_count": 0, "data": [], "errors": [{"symbol": "ABC", "error": "margin_api_failed"}]}
    parsed = json.loads(json.dumps(fixture))
    api = API_SERVICE.read_text(encoding="utf-8")
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert parsed["errors"][0]["symbol"] == "ABC"
    assert parsed["errors"][0]["error"] == "margin_api_failed"
    assert "data class CashFutureScanError" in api
    assert "error.symbol" in ui
    assert "error.error" in ui


def test_android_cash_future_scanner_states_are_clear():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'btnRunScanner.text = "SCANNING LIVE..."' in ui
    assert "LIVE SCAN IN PROGRESS" in ui
    assert "Reading Cash–Future 1-second signals..." in ui
    assert 'append("LIVE 1s SCAN — \\n")' in ui
    assert 'append("LIVE 1s SCAN — \\n")' in ui
    assert '"SCAN ERROR\\n\\nLast Attempt: $failedAt\\n\\nScanner Failed:' in ui


def test_android_cash_future_scanner_summary_counts_are_clear():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'append("Current signals: ${sorted.size}\\n")' in ui
    assert 'append("Executable positive-gap signals: ${sorted.count { it.gap > 0.0 && it.net_gap > 0.0 }}\\n")' in ui
    assert 'append("Source: Angel One WebSocket → 1s collector → live scanner\\n\\n")' in ui
    assert 'append("LIVE 1s SCAN — ")' in ui


def test_android_cash_future_per_stock_summary_is_clear():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    for label in ('append("────────────────────\\n")', 'append("Cash: ₹${item.cash_ask ?: item.cash_ltp}\\n")', 'append("Future: ₹${item.future_bid ?: item.future_ltp}\\n")', 'append("Gap: ₹${item.gap} (${item.gap_pct}%)\\n")', 'append("Gross Spread: ₹${item.gross_profit ?: item.gross_lot_value ?: 0.0}\\n")', 'append("Margin: ₹${item.capacity_notional ?: 0.0}\\n")', 'append("Deployed Capital: ₹${item.capacity_notional ?: 0.0}\\n")', 'append("Net Profit: ₹${item.net_profit ?: 0.0}\\n")', 'append("ROI: ${item.net_gap_pct}%\\n")', 'append("Lifecycle: ${item.lifecycle}\\n\\n")'):
        assert label in ui


def test_android_cash_future_opportunities_are_prioritized():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'append("Current signals:\\n")' in ui
    assert '.sortedWith(compareByDescending<LiveCashFutureSignal> { it.executable }.thenByDescending { it.roi_pct }.thenByDescending { it.net_profit })' in ui


def test_android_cash_future_last_scan_time_is_completion_time():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    response_marker = 'val response = ApiService.retrofitService.liveCashFutureScan(maxAgeSeconds = 5.0, limit = 50)'
    completion_marker = 'val completedAt = currentTimestamp()'
    success_marker = 'append("Last Scan: $completedAt\\n\\n")'
    start = ui.index(response_marker)
    segment = ui[start:start + 1200]
    assert completion_marker in segment
    assert success_marker in segment
    assert segment.index(response_marker) < segment.index(completion_marker) < segment.index(success_marker)


def test_android_cash_future_error_timestamp_is_recorded_on_failure():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'val failedAt = currentTimestamp()' in ui
    assert 'Last Attempt: $failedAt' in ui
    assert 'Scanner Failed: ${error.message ?: "API error"}' in ui


