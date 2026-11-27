from pathlib import Path

ROOT = Path(__file__).parents[2]
MAIN_ACTIVITY = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
LAYOUT = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml"


def test_cash_future_scanner_controls_show_disabled_state():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    layout = LAYOUT.read_text(encoding="utf-8")
    assert 'id="@+id/tvScannerAutoRefreshStatus"' in layout
    assert 'id="@+id/tvScannerNextRefresh"' in layout
    assert 'tvScannerAutoRefreshStatus.text = "Auto Refresh: DISABLED"' in body
    assert 'tvScannerNextRefresh.text = "Next Refresh: DISABLED"' in body
    assert 'btnRunScanner.text = "CASH-FUTURE SCANNER DISABLED"' in body
    assert "cbScannerAutoRefresh.isEnabled = false" in body
    assert "etScannerRefreshSeconds.isEnabled = false" in body


def test_cash_future_scanner_has_no_scheduled_or_network_refresh():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "private fun runCashFutureScanner" not in body
    assert "liveCashFutureScan(" not in body
    assert "Handler(Looper.getMainLooper())" not in body
    assert "postDelayed(" not in body
    assert "scheduleScannerRefresh" not in body
    assert "scannerRefreshRunnable" not in body
    assert "override fun onDestroy()" in body
