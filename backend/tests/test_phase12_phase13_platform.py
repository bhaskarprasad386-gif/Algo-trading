from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANDROID_LAYOUT = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml"
ANDROID_MAIN = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"
FRONTEND_HOME = ROOT / "frontend" / "app" / "page.tsx"


def test_phase12_current_web_android_alignment():
    home = FRONTEND_HOME.read_text(encoding="utf-8")
    layout = ANDROID_LAYOUT.read_text(encoding="utf-8")
    main = ANDROID_MAIN.read_text(encoding="utf-8")

    assert 'const markets = ["NIFTY", "BANKNIFTY"]' in home
    assert "/api/v1/market-data/overview" in home
    assert "Box Spread" in home
    assert "Cash-Future" not in home
    assert "Calendar Spread" not in home
    assert "Synthetic Arbitrage" not in home

    assert "PHASE 12 • MOBILE NAVIGATION" in layout
    assert 'android:id="@+id/mainScroll"' in layout
    for view_id in ("btnQuickMarket", "btnQuickScanner", "btnQuickStrategies", "btnQuickResults", "btnQuickExpansion"):
        assert view_id in layout
    assert "Cash-Future" not in layout
    assert "CASH–FUTURE SCANNER" not in layout

    assert "btnQuickMarket.setOnClickListener" in main
    assert "btnQuickScanner.setOnClickListener" in main
    assert "btnQuickStrategies.setOnClickListener" in main
    assert "btnQuickResults.setOnClickListener" in main
    assert "btnQuickExpansion.setOnClickListener" in main


def test_phase13_android_expansion_layer_remains_available():
    layout = ANDROID_LAYOUT.read_text(encoding="utf-8")
    assert "PHASE 13 • EXPANSION CENTER" in layout
    for label in ("INDICES / EXCHANGES", "COMMODITIES / CURRENCY", "STRATEGIES / SCANNERS", "DATA SOURCES", "ANALYTICS / CHARTS", "NOTIFICATIONS", "BROKER ADAPTERS", "USER-SPECIFIC LAYOUTS"):
        assert label in layout
    assert "Real broker routing remains OFF." in layout
