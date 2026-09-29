from pathlib import Path

from app.main import DASHBOARD_FILE, dashboard


ROOT = Path(__file__).resolve().parents[2]
ANDROID_LAYOUT = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml"
ANDROID_MAIN = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_phase12_web_android_alignment():
    body = dashboard().body.decode("utf-8")
    assert "PHASE 12 • PLATFORM NAVIGATION" in body
    assert 'id="responsiveQuickNav"' in body
    assert "WEB • FULL TABLES" in body
    assert "ANDROID • COMPACT CARDS" in body
    assert "SAME API / DATA" in body
    assert "PAPER ORDERS ONLY" in body

    layout = ANDROID_LAYOUT.read_text()
    assert "PHASE 12 • MOBILE NAVIGATION" in layout
    assert 'android:id="@+id/mainScroll"' in layout
    for view_id in ("btnQuickMarket", "btnQuickScanner", "btnQuickStrategies", "btnQuickResults", "btnQuickPaper", "btnQuickExpansion"):
        assert view_id in layout

    main = ANDROID_MAIN.read_text()
    assert "btnQuickMarket.setOnClickListener" in main
    assert "btnQuickScanner.setOnClickListener" in main
    assert "btnQuickStrategies.setOnClickListener" in main
    assert "btnQuickResults.setOnClickListener" in main
    assert "btnQuickPaper.setOnClickListener" in main
    assert "btnQuickExpansion.setOnClickListener" in main


def test_phase13_final_expansion_layer_is_present_on_both_surfaces():
    body = dashboard().body.decode("utf-8")
    assert "PHASE 13 • EXPANSION CENTER" in body
    for label in ("INDICES &amp; EXCHANGES", "COMMODITIES &amp; CURRENCY", "STRATEGIES &amp; SCANNERS", "DATA SOURCES", "ANALYTICS &amp; CHARTS", "NOTIFICATIONS", "BROKER INTEGRATIONS", "USER LAYOUTS"):
        assert label in body
    assert "Real broker routing remains OFF." in body

    layout = ANDROID_LAYOUT.read_text()
    assert "PHASE 13 • EXPANSION CENTER" in layout
    for label in ("INDICES / EXCHANGES", "COMMODITIES / CURRENCY", "STRATEGIES / SCANNERS", "DATA SOURCES", "ANALYTICS / CHARTS", "NOTIFICATIONS", "BROKER ADAPTERS", "USER-SPECIFIC LAYOUTS"):
        assert label in layout
    assert "Real broker routing remains OFF." in layout
