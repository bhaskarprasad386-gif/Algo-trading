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
        'showTradingJournal()',
        'showHistoricalReplay()',
        'openBrokerSettings()',
        'touch-action:manipulation',
        'env(safe-area-inset-bottom)',
    ):
        assert marker in ui


def test_batch13_keeps_live_bus_without_paper_controls():
    ui = WEB.read_text(encoding="utf-8")
    assert 'id="liveChannelStrip"' in ui
    assert 'Live broker orders: OFF' in ui
    assert 'SCANNER ONLY' in ui
    assert 'PAPER ONLY' not in ui
    assert 'Paper Portfolio' not in ui
    assert 'showPaperPortfolio' not in ui
