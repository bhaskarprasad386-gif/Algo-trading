from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "dashboard" / "index.html"


def test_batch1_home_command_center_contract():
    ui = WEB.read_text(encoding="utf-8")
    for marker in (
        "Home / Command Center",
        "Market Overview",
        "Custom Alerts",
        "Live Scanner",
        "Strategy Opportunities",
        "Recent Alerts",
        "Live Market Chart",
        "System Status",
        "Live Data Quality Center",
        "mobile",
        "bottom-nav",
        "DASHBOARD_WIDGET_KEY",
        "saveDashboardWidgets",
        "resetDashboardWidgets",
        "/api/v1/market-data/overview",
        "/api/v1/market-data/live-health",
        "/api/v1/scanner/cash-future/live/fast",
        "/ws/dashboard",
        "connectDashboardWebSocket",
        "WS CONNECTED",
        "Live broker orders: OFF",
    ):
        assert marker in ui


def test_batch1_home_has_backend_strategy_workspace_contract():
    ui = WEB.read_text(encoding="utf-8")
    assert "/api/v1/app/strategies" in ui
    assert "/api/v1/app/strategies/'+encodeURIComponent(id)+'/workspace" in ui
    assert "OPEN WORKSPACE" in ui or "Workspace contracts" in ui
    for strategy in ("cash-future", "calendar-spread", "synthetic-future-cash-carry", "box-spread"):
        assert strategy in ui


def test_batch1_home_has_no_paper_execution_contract():
    ui = WEB.read_text(encoding="utf-8")
    assert "Paper Portfolio" not in ui
    assert "/api/v1/execution/paper/" not in ui
    assert "/api/v1/live-paper/" not in ui
