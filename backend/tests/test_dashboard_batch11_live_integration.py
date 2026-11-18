from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "backend" / "app" / "main.py"
WEB = ROOT / "web" / "dashboard" / "index.html"


def test_batch11_websocket_publishes_unified_live_bus():
    src = MAIN.read_text(encoding="utf-8")
    for marker in (
        '@app.websocket("/ws/dashboard")',
        '"integration": {',
        '"calendar_spread":',
        '"synthetic_arbitrage":',
        '"box_spread":',
        '"broker_socket_per_browser": False',
        '"live_orders": "OFF"',
    ):
        assert marker in src


def test_batch11_dashboard_uses_one_live_bus_and_refreshes_active_workspace():
    ui = WEB.read_text(encoding="utf-8")
    for marker in (
        'id="liveBus"',
        'id="liveChannelStrip"',
        'function updateLiveBus(',
        'function scheduleLiveWorkspaceRefresh(',
        'connectDashboardWebSocket()',
        'updateLiveBus(payload.integration,true)',
        'LIVE BUS',
        'BROKER ORDERS OFF',
    ):
        assert marker in ui
