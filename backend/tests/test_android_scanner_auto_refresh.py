from pathlib import Path


MAIN_ACTIVITY = Path(__file__).parents[2] / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
LAYOUT = Path(__file__).parents[2] / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml"


def test_android_scanner_auto_refresh_controls():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    layout = LAYOUT.read_text(encoding="utf-8")

    assert 'id="@+id/cbScannerAutoRefresh"' in layout
    assert 'id="@+id/etScannerRefreshSeconds"' in layout
    assert 'text="30"' in layout
    assert "coerceIn(10L, 300L)" in body
    assert "scannerRefreshHandler.postDelayed(scannerRefreshRunnable, seconds * 1000L)" in body
    assert "scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)" in body
    assert "scheduleScannerRefresh()" in body
    assert "cbScannerAutoRefresh.setOnCheckedChangeListener" in body
    assert "if (!cbScannerAutoRefresh.isChecked || btnRunScanner.isEnabled.not())" in body
    assert "return" in body.split("if (!cbScannerAutoRefresh.isChecked || btnRunScanner.isEnabled.not())", 1)[1]
    assert "scheduleScannerRefresh()" in body.split("finally", 1)[1]
