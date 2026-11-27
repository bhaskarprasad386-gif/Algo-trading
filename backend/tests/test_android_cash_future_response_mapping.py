from pathlib import Path

ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
API_SERVICE = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_cash_future_scanner_is_disabled_and_no_longer_calls_live_apis():
    api = API_SERVICE.read_text(encoding="utf-8")
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'GET("/api/v1/scanner/cash-future/live/fast")' not in api
    assert 'GET("/api/v1/scanner/cash-future/live/auto")' not in api
    assert "liveCashFutureScan(" not in ui
    assert "cashFutureScan()" not in api
    assert "runCashFutureScanner" not in ui
    assert "Cash-Future live scanning is disabled." in ui
    assert "No scanner API request will be sent." in ui
