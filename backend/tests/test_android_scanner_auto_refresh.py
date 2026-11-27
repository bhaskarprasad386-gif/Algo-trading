from pathlib import Path


MAIN_ACTIVITY = Path(__file__).parents[2] / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
LAYOUT = Path(__file__).parents[2] / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml"


def test_android_cash_future_scanner_is_disabled_without_auto_refresh():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    layout = LAYOUT.read_text(encoding="utf-8")

    assert 'id="@+id/cbScannerAutoRefresh"' in layout
    assert 'id="@+id/etScannerRefreshSeconds"' in layout
    assert 'text="30"' in layout
    assert "disableCashFutureScanner()" in body
    assert "cbScannerAutoRefresh.isEnabled = false" in body
    assert "etScannerRefreshSeconds.isEnabled = false" in body
    assert "CASH-FUTURE SCANNER DISABLED" in body
    assert "runCashFutureScanner" not in body
    assert "scannerRefreshHandler.postDelayed" not in body
    assert "scannerRefreshRunnable" not in body
