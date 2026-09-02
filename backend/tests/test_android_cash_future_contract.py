from pathlib import Path


ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
API_SERVICE = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_cash_future_api_contract_matches_backend():
    body = API_SERVICE.read_text(encoding="utf-8")
    assert 'GET("/api/v1/scanner/cash-future/live/auto")' in body
    assert "val symbols_requested: List<String>" in body
    assert "val scanned_observations: Int" in body
    assert "val opportunity_count: Int" in body
    assert "val gross_spread_profit: Double" in body
    assert "val deployed_capital: Double" in body
    assert "data class CashFutureScanError" in body
    assert "val errors: List<CashFutureScanError>" in body


def test_android_cash_future_screen_is_wired_to_scanner():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'btnRunScanner.setOnClickListener' in body
    assert 'cashFutureScan()' in body
    assert 'response.symbols_requested.size' in body
    assert 'response.scanned_observations' in body
    assert 'response.opportunity_count' in body
    assert 'response.errors.size' in body
