from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "dashboard" / "index.html"


def test_batch6_box_spread_workspace_ui_contract():
    ui = WEB.read_text(encoding="utf-8")
    for marker in (
        'id="boxSpreadNav"',
        'id="boxSpreadPage"',
        "Box Spread Workspace",
        "/api/v1/scanner/box-spread/live?limit=200",
        "/api/v1/scanner/box-spread/alerts?days=30",
        "LOW STRIKE • CALL / PUT",
        "HIGH STRIKE • CALL / PUT",
        'onclick="selectBox(',
        "function boxPaperEntry()",
        "box-mobile-actions",
        "connectDashboardWebSocket",
        "Real broker routing remains OFF",
        "₹1 crore paper ledger",
    ):
        assert marker in ui
