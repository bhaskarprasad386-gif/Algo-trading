from pathlib import Path

ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
API_SERVICE = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
FULL_FNO_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "FullFnoBacktestActivity.kt"


def test_android_client_has_no_cash_future_scanner_api_or_home_controls():
    api = API_SERVICE.read_text(encoding="utf-8")
    home = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "cashFuture" not in api
    assert "LiveCashFutureSignal" not in api
    assert "cash-future" not in home.lower()
    assert "disableCashFutureScanner" not in home
    assert "btnRunScanner" not in home
    assert "btnBoxSpread" in home


def test_android_keeps_only_box_spread_live_scanner_and_full_fno_history():
    api = API_SERVICE.read_text(encoding="utf-8")
    home = MAIN_ACTIVITY.read_text(encoding="utf-8")
    full_fno = FULL_FNO_ACTIVITY.read_text(encoding="utf-8")
    assert 'GET("/api/v1/scanner/box-spread/live")' in api
    assert "BoxSpreadActivity" in home
    assert "startFullFnoJob()" in full_fno
    assert "cashFutureReplay(" not in full_fno
    assert "calendarSpreadHistoricalReplay(" not in api
