from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME = ROOT / "frontend" / "app" / "page.tsx"
SHELL = ROOT / "frontend" / "components" / "app-shell.tsx"


def test_current_home_is_nifty_banknifty_and_box_spread_only():
    ui = HOME.read_text(encoding="utf-8")
    shell = SHELL.read_text(encoding="utf-8")
    assert 'const markets = ["NIFTY", "BANKNIFTY"]' in ui
    assert "Box Spread" in ui
    assert "/strategies/box-spread" in shell
    for retired in ("Cash-Future", "Calendar Spread", "Synthetic Arbitrage", "Paper Trading"):
        assert retired not in ui
        assert retired not in shell


def test_current_home_uses_shared_market_overview_without_retired_scanner_routes():
    ui = HOME.read_text(encoding="utf-8")
    assert "/api/v1/market-data/overview" in ui
    assert "/api/v1/scanner/cash-future" not in ui
    assert "/api/v1/scanner/calendar-spread" not in ui
    assert "/api/v1/scanner/synthetic" not in ui
