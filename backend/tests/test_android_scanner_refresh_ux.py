from pathlib import Path


ROOT = Path(__file__).parents[2]
MAIN_ACTIVITY = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
LAYOUT = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml"


def test_android_scanner_refresh_ux_contract():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    layout = LAYOUT.read_text(encoding="utf-8")

    assert 'id="@+id/tvScannerAutoRefreshStatus"' in layout
    assert 'id="@+id/tvScannerNextRefresh"' in layout
    assert 'tvScannerAutoRefreshStatus.text = if (cbScannerAutoRefresh.isChecked) "Auto Refresh: ON" else "Auto Refresh: OFF"' in body
    assert 'tvScannerNextRefresh.text = "Next Refresh: ${scannerCountdownSeconds}s"' in body
    assert 'scannerRefreshHandler.postDelayed(scannerCountdownRunnable, 1000L)' in body
    condition = 'if (!cbScannerAutoRefresh.isChecked || btnRunScanner.isEnabled.not())'
    equivalent_condition = 'if (!cbScannerAutoRefresh.isChecked || !btnRunScanner.isEnabled)'
    assert condition in body or equivalent_condition in body
    assert 'tvScannerNextRefresh.text = if (btnRunScanner.isEnabled) "Next Refresh: —" else "Next Refresh: SCAN IN PROGRESS"' in body

    scan_block = body.split('private fun runCashFutureScanner()', 1)[1]
    assert 'scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)' in scan_block
    assert 'scannerRefreshHandler.removeCallbacks(scannerCountdownRunnable)' in scan_block
    assert 'btnRunScanner.text = "SCANNING..."' in scan_block
    assert 'scheduleScannerRefresh()' in scan_block.split('finally', 1)[1]

    destroy = body.split('override fun onDestroy()', 1)[1]
    assert 'scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)' in destroy
    assert 'scannerRefreshHandler.removeCallbacks(scannerCountdownRunnable)' in destroy
    assert 'toLongOrNull()?.coerceIn(10L, 300L) ?: 30L' in body
