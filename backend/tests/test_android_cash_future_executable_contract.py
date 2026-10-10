from pathlib import Path

ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
API_SERVICE = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
AUTO_ROUTES = Path(__file__).resolve().parents[1] / "app" / "scanner" / "auto_routes.py"

def test_android_cash_future_executable_contract_matches_backend():
    api = API_SERVICE.read_text(encoding="utf-8")
    backend = AUTO_ROUTES.read_text(encoding="utf-8")
    assert 'GET("/api/v1/scanner/cash-future/live/fast")' in api
    for field in ("symbols_requested", "scanned_observations", "opportunity_count", "data", "errors", "filters"):
        assert f'"{field}"' in backend
    for field in ("symbol", "cash_ask", "future_bid", "gap", "gap_pct", "net_gap", "net_profit", "lifecycle", "rank_score"):
        assert f"val {field}:" in api

def test_android_cash_future_screen_displays_scanner_opportunities_without_paper_execution():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "response.data" in body
    assert ".sortedWith(compareByDescending<LiveCashFutureSignal> { it.executable }" in body
    assert "lastExecutableOpportunity = executable" in body
    assert "openScannerDetail(executable)" in body
    assert "btnScannerPaperExecute" not in body
    assert "paperEntry(" not in body
    assert "paperExit(" not in body
