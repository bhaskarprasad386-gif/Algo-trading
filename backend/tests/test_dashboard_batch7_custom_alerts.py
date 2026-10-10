from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web" / "dashboard" / "index.html"

def test_batch7_custom_alerts_ui_contract():
    ui = WEB.read_text(encoding="utf-8")
    for marker in (
        'id="alertsNav"',
        'id="customAlertsPage"',
        "Custom Alerts",
        "CASH-FUTURE",
        "CALENDAR SPREAD",
        "SYNTHETIC ARBITRAGE",
        "BOX SPREAD",
        "Executable Gap / Edge",
        "Volume",
        "OI Change",
        "Spread %",
        "Premium",
        "AND",
        "OR",
        "App notification",
        "Sound",
        "Push",
        "ACTIVE",
        "TRIGGERED",
        "SAVED",
        "function createCustomAlert()",
        "function toggleCustomAlert(",
        "function deleteCustomAlert(",
        "localStorage",
        "never place broker orders",
        "Configure scanner alert notifications",
    ):
        assert marker in ui
