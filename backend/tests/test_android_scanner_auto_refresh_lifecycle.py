from pathlib import Path


MAIN_ACTIVITY = Path(__file__).parents[2] / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_scanner_auto_refresh_is_disabled_without_scheduled_work():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "disableCashFutureScanner();" in body
    assert "cbScannerAutoRefresh.isEnabled = false" in body
    assert "etScannerRefreshSeconds.isEnabled = false" in body
    assert "scannerRefreshHandler.postDelayed" not in body
    assert "scannerRefreshRunnable" not in body
    assert "private fun runCashFutureScanner" not in body
    assert "override fun onDestroy()" in body
