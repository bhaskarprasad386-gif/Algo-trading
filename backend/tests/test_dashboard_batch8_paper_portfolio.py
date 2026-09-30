from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web" / "dashboard" / "index.html"


def test_batch8_paper_portfolio_workspace_contract():
    ui = WEB.read_text(encoding="utf-8")
    for marker in (
        'id="paperPortfolioNav"',
        'id="paperPortfolioPage"',
        "Paper Execution Ledger",
        "₹1 crore virtual capital",
        "/api/v1/execution/paper/account",
        "/api/v1/execution/paper/orders",
        "/api/v1/execution/paper/position",
        "/api/v1/execution/paper/exit",
        "function showPaperPortfolio",
        "function refreshPaperPortfolio",
        "paper-mobile-actions",
        "BROKER ORDERS",
        "PAPER ONLY",
    ):
        assert marker in ui


def test_batch8_paper_workspace_uses_existing_dashboard_websocket():
    ui = WEB.read_text(encoding="utf-8")
    assert "/ws/dashboard" in ui
    assert "connectDashboardWebSocket" in ui
    assert "refreshPaperPortfolio(false)" in ui
