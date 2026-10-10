from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "dashboard" / "index.html"
ANDROID_LAYOUT = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_main.xml"
ANDROID_MAIN = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_phase2_web_persistent_widget_preferences():
    ui = WEB.read_text(encoding="utf-8")
    assert "DASHBOARD_WIDGET_KEY" in ui
    assert "localStorage" in ui
    assert "saveDashboardWidgets" in ui
    assert "resetDashboardWidgets" in ui
    for widget in ("market", "scanner", "strategy", "results", "terminal"):
        assert f'data-widget="{widget}"' in ui or f'data-widget-id="{widget}"' in ui


def test_phase2_android_persistent_dashboard_preferences():
    ui = WEB.read_text(encoding="utf-8")
    layout = ANDROID_LAYOUT.read_text(encoding="utf-8")
    main = ANDROID_MAIN.read_text(encoding="utf-8")
    for marker in ("dashboardCustomization", "cbShowMarket", "cbShowScanner", "cbShowStrategy", "cbShowResults", "btnSaveDashboardLayout", "btnResetDashboardLayout"):
        assert marker in layout
    assert 'getSharedPreferences("dashboard_layout"' in main
    assert "saveDashboardLayout" in main
    assert "resetDashboardLayout" in main
    assert "applyDashboardVisibility" in main
    assert "View.GONE" in main
    assert "Layout: SAVED" in main
    assert "cbShowPaper" not in layout
    assert "data-widget=\"paper\"" not in ui
