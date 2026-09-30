from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "dashboard" / "index.html"


def test_batch13_full_ux_mobile_workspace_launcher_contract():
    ui = WEB.read_text(encoding="utf-8")
    for marker in (
        'id="workspaceLauncher"',
        'WORKSPACE LAUNCHER',
        'function openWorkspaceLauncher()',
        'function closeWorkspaceLauncher()',
        'function setMobileNav(',
        'id="responsiveQuickNav"',
        'showCashFuture()',
        'showCalendarSpread()',
        'showSyntheticArbitrage()',
        'showBoxSpread()',
        'showCustomAlerts()',
        'showPaperPortfolio()',
        'showTradingJournal()',
        'showHistoricalReplay()',
        'openBrokerSettings()',
        'touch-action:manipulation',
        'env(safe-area-inset-bottom)',
    ):
        assert marker in ui


def test_batch13_keeps_live_bus_and_paper_only_contract():
    ui = WEB.read_text(encoding="utf-8")
    assert 'id="liveChannelStrip"' in ui
    assert 'BROKER ORDERS OFF' in ui
    assert 'PAPER ONLY' in ui
