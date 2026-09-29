from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "dashboard" / "index.html"
ANDROID_LAYOUT = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml"
ANDROID_MAIN = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
ANDROID_API = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"


def test_phase1_web_market_control_center_contract():
    ui = WEB.read_text(encoding="utf-8")
    assert "MARKET WATCH" in ui.upper()
    assert 'id="indexRows"' in ui
    assert 'id="commodityRows"' in ui
    assert "/api/v1/market-data/overview" in ui
    assert "Angel One" in ui
    assert "ORDERS OFF" in ui


def test_phase1_android_market_control_center_contract():
    layout = ANDROID_LAYOUT.read_text(encoding="utf-8")
    main = ANDROID_MAIN.read_text(encoding="utf-8")
    api = ANDROID_API.read_text(encoding="utf-8")
    for marker in ("marketControlCenter", "tvMarketFeedStatus", "tvIndexOverview", "tvCommodityOverview", "btnMarketOverviewRefresh"):
        assert marker in layout
        assert marker in main
    assert "marketOverview()" in api
    assert "/api/v1/market-data/overview" in api
    assert "Orders: OFF" in main
