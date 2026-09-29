from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "dashboard" / "index.html"
ANDROID = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "StrategyRegistryActivity.kt"
API = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"


def test_phase4_shared_strategy_workspace_contract():
    web = WEB.read_text(encoding="utf-8")
    android = ANDROID.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    assert "/api/v1/app/strategies" in web
    assert "/api/v1/app/strategies/'+encodeURIComponent(id)+'/workspace" in web
    assert "OPEN WORKSPACE" in web
    assert "capabilities" in web
    assert "live_orders" in web

    assert "appStrategies()" in android
    assert "strategyWorkspace" in api
    assert "item.capabilities" in android
    assert "item.live_orders" in android

    for strategy in ("cash-future", "calendar-spread", "synthetic-future-cash-carry", "box-spread", "full-fno"):
        assert strategy in web or strategy in android
