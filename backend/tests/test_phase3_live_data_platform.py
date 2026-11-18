from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "dashboard" / "index.html"
ANDROID_LAYOUT = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml"
ANDROID_MAIN = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
ANDROID_API = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"


def test_phase3_live_data_health_contract_is_shared():
    web = WEB.read_text(encoding="utf-8")
    layout = ANDROID_LAYOUT.read_text(encoding="utf-8")
    main = ANDROID_MAIN.read_text(encoding="utf-8")
    api = ANDROID_API.read_text(encoding="utf-8")

    assert "/api/v1/market-data/live-health" in web
    assert "loadLiveDataHealth" in web
    assert "liveDataHealth" in api
    assert "LiveDataHealthResponse" in api
    for marker in ("liveDataQuality", "tvLiveDataQuality", "btnLiveDataHealthRefresh"):
        assert marker in layout
    for marker in ("loadLiveDataHealth", "btnLiveDataHealthRefresh.setOnClickListener", "tvLiveDataQuality.visibility"):
        assert marker in main
    assert "Live broker orders: OFF" in web
    assert "Live broker orders: OFF" in layout
