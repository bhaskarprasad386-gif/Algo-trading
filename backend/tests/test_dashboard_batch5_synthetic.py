from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "dashboard" / "index.html"


def test_batch5_synthetic_workspace_ui_contract():
    ui = WEB.read_text(encoding="utf-8")
    for marker in (
        'id="syntheticNav"',
        'id="syntheticPage"',
        "Synthetic Arbitrage Workspace",
        "INDICES • ATM ±10",
        "NIFTY 50 • ATM ±5",
        "/api/v1/scanner/synthetic-cash-carry/live?limit=200",
        "/api/v1/scanner/synthetic-cash-carry/alerts?days=30",
        "FUTURE BID / ASK",
        "CALL BID / ASK",
        "PUT BID / ASK",
        "function selectSynthetic(",
        "function synPaperEntry()",
        "syn-mobile-actions",
        "connectDashboardWebSocket",
        "real broker routing remains OFF",
    ):
        assert marker in ui
