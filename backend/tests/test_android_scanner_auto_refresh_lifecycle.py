from pathlib import Path


MAIN_ACTIVITY = Path(__file__).parents[2] / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_scanner_auto_refresh_lifecycle_and_reschedule():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")

    assert "private lateinit var scannerRefreshRunnable: Runnable" in body
    assert "scannerRefreshRunnable = Runnable { runCashFutureScanner() }" in body
    assert "override fun onDestroy()" in body
    assert "scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)" in body
    assert "private fun scheduleScannerRefresh()" in body
    assert "scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)" in body.split("private fun scheduleScannerRefresh()", 1)[1]
    assert "scannerRefreshHandler.postDelayed(scannerRefreshRunnable, seconds * 1000L)" in body
    assert "finally" in body and "scheduleScannerRefresh()" in body.split("finally", 1)[1]
