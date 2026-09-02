from pathlib import Path


MAIN_ACTIVITY = Path(__file__).parents[2] / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_scanner_auto_refresh_behavior_contract():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")

    assert "toLongOrNull()?.coerceIn(10L, 300L) ?: 30L" in body
    assert "etScannerRefreshSeconds.setText(seconds.toString())" in body

    assert "scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)" in body
    assert "btnRunScanner.isEnabled = false" in body
    assert 'btnRunScanner.text = "SCANNING..."' in body

    schedule = body.split("private fun scheduleScannerRefresh()", 1)[1]
    assert "scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)" in schedule
    assert "if (!cbScannerAutoRefresh.isChecked || btnRunScanner.isEnabled.not())" in schedule
    assert "return" in schedule.split("if (!cbScannerAutoRefresh.isChecked || btnRunScanner.isEnabled.not())", 1)[1]
    assert "scannerRefreshHandler.postDelayed(scannerRefreshRunnable, seconds * 1000L)" in schedule

    finally_block = body.split("finally", 1)[1]
    assert "btnRunScanner.isEnabled = true" in finally_block
    assert 'btnRunScanner.text = "RUN CASH–FUTURE SCAN"' in finally_block
    assert "scheduleScannerRefresh()" in finally_block

    assert "private lateinit var scannerRefreshRunnable: Runnable" in body
    assert "scannerRefreshRunnable = Runnable { runCashFutureScanner() }" in body
    assert "override fun onDestroy()" in body
