from pathlib import Path


ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
API_SERVICE = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_cash_future_api_contract_matches_backend():
    body = API_SERVICE.read_text(encoding="utf-8")
    assert 'GET("/api/v1/scanner/cash-future/live/fast")' in body
    assert "val symbol: String" in body
    assert "val contract_month: String" in body
    assert "val cash_ask: Double?" in body
    assert "val future_bid: Double?" in body
    assert "val gap: Double" in body
    assert "val net_gap: Double" in body
    assert "val net_profit: Double?" in body
    assert "val lifecycle: String" in body
    assert "val rank_score: Double" in body


def test_android_cash_future_screen_is_wired_to_scanner():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'btnRunScanner = findViewById(R.id.btnRunScanner)' in body
    assert 'btnRunScanner.setOnClickListener { runCashFutureScanner() }' in body
    assert 'liveCashFutureScan(maxAgeSeconds = 5.0, limit = 50)' in body
    assert 'btnRunScanner.isEnabled = false' in body
    assert 'btnRunScanner.text = "SCANNING LIVE..."' in body
    assert 'btnRunScanner.isEnabled = true' in body
    assert 'response.data.size' in body
    assert 'response.data.size' in body
    assert 'response.data.count { it.gap > 0.0 && it.net_gap > 0.0 }' in body
    assert 'response.data.count { it.lifecycle == "EXPIRED" }' in body


def test_android_cash_future_screen_shows_error_reasons():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "item.lifecycle" in body
    assert "item.alert_event" in body
    assert "item.stable_observations" in body
