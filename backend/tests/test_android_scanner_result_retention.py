from pathlib import Path

MAIN_ACTIVITY = Path(__file__).parents[2] / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_disabled_cash_future_screen_does_not_retain_stale_results():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "Cash-Future live scanning is disabled." in body
    assert "Cash-Future scanner alerts are disabled." in body
    assert "lastScannerResult" not in body
    assert "lastExecutableOpportunity" not in body
    assert "openScannerDetail" not in body
    assert "runCashFutureScanner" not in body
