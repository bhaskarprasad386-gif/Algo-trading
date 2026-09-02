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
    for field in ("cash_price", "future_price", "gap", "gap_pct", "gross_spread_profit", "margin_required", "deployed_capital", "net_profit", "roi_pct"):
        assert f"item.{field}" in ui
    assert 'if (item.executable) "YES" else "NO"' in ui


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
    assert 'btnRunScanner.text = "SCANNING..."' in ui
    assert 'tvScannerResult.text = "SCAN IN PROGRESS\\n\\nRunning Cash–Future scanner..."' in ui
    assert 'append("SCAN COMPLETE — SUCCESS\\n")' in ui
    assert 'append("SCAN COMPLETE — NO OPPORTUNITIES\\n")' in ui
    assert 'tvScannerResult.text = "SCAN ERROR\\n\\nScanner Failed:' in ui


def test_android_cash_future_scanner_summary_counts_are_clear():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'append("Symbols requested: ${response.symbols_requested.size}\\n")' in ui
    assert 'append("Observations: ${response.scanned_observations}\\n")' in ui
    assert 'append("Executable opportunities: ${response.opportunity_count}\\n")' in ui
    assert 'append("Errors: ${response.errors.size}\\n\\n")' in ui
    assert 'append("Executable opportunities: 0\\n")' in ui


def test_android_cash_future_per_stock_summary_is_clear():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    for label in ('append("────────────────────\\n")', 'append("Cash: ₹${item.cash_price}\\n")', 'append("Future: ₹${item.future_price}\\n")', 'append("Gap: ₹${item.gap} (${item.gap_pct}%)\\n")', 'append("Gross Spread: ₹${item.gross_spread_profit}\\n")', 'append("Margin: ₹${item.margin_required}\\n")', 'append("Deployed Capital: ₹${item.deployed_capital}\\n")', 'append("Net Profit: ₹${item.net_profit}\\n")', 'append("ROI: ${item.roi_pct}%\\n")', 'append("Executable: ${if (item.executable) "YES" else "NO"}\\n\\n")'):
        assert label in ui


def test_android_cash_future_opportunities_are_prioritized():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'append("Priority: EXECUTABLE FIRST\\n")' in ui
    assert '.sortedWith(compareByDescending<CashFutureOpportunity> { it.executable }.thenByDescending { it.roi_pct }.thenByDescending { it.net_profit })' in ui


def test_android_cash_future_last_scan_time_is_displayed():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'SimpleDateFormat("dd-MM-yyyy HH:mm:ss", Locale.getDefault())' in ui
    assert 'append("Last Scan: $scannedAt\\n\\n")' in ui
    assert 'tvScannerResult.text = "SCAN ERROR\\n\\nLast Scan: ${lastScanTime()}\\n\\nScanner Failed:' in ui
