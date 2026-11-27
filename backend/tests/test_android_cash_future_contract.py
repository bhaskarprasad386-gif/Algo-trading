from pathlib import Path


ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
API_SERVICE = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_cash_future_live_api_clients_are_removed():
    api = API_SERVICE.read_text(encoding="utf-8")
    activity = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'GET("/api/v1/scanner/cash-future/live/fast")' not in api
    assert 'GET("/api/v1/scanner/cash-future/live/auto")' not in api
    assert "liveCashFutureScan(" not in activity
    assert "cashFutureScan()" not in api


def test_android_cash_future_screen_is_explicitly_disabled_without_network_calls():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'btnRunScanner = findViewById(R.id.btnRunScanner)' in body
    assert 'disableCashFutureScanner();' in body
    assert 'btnRunScanner.text = "CASH-FUTURE SCANNER DISABLED"' in body
    assert 'tvScannerAutoRefreshStatus.text = "Auto Refresh: DISABLED"' in body
    assert 'tvScannerNextRefresh.text = "Next Refresh: DISABLED"' in body
    assert "No scanner API request will be sent." in body
    assert "runCashFutureScanner" not in body


def test_android_cash_future_disabled_screen_retains_non_network_notice():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "Cash-Future scanner alerts are disabled." in body
    assert "btnQuickScanner.setOnClickListener" in body
