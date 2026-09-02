from pathlib import Path

ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
API_SERVICE = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
AUTO_ROUTES = Path(__file__).resolve().parents[1] / "app" / "scanner" / "auto_routes.py"

def test_android_cash_future_executable_contract_matches_backend():
    api = API_SERVICE.read_text(encoding="utf-8")
    backend = AUTO_ROUTES.read_text(encoding="utf-8")
    assert 'GET("/api/v1/scanner/cash-future/live/auto")' in api
    for field in ("symbols_requested", "scanned_observations", "opportunity_count", "data", "errors", "filters"):
        assert f'"{field}"' in backend
    for field in ("symbol", "cash_price", "future_price", "gap", "gap_pct", "gross_spread_profit", "margin_required", "deployed_capital", "net_profit", "roi_pct", "executable"):
        assert f"val {field}:" in api

def test_android_cash_future_screen_displays_executable_status():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "response.data" in body
    assert '.sortedWith(compareByDescending<CashFutureOpportunity> { it.executable }' in body
    assert 'if (item.executable) "YES" else "NO"' in body
    assert 'append("Executable:' in body
