from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_WEB = ROOT / "web" / "dashboard" / "index.html"
HOME = ROOT / "frontend" / "app" / "page.tsx"
SHELL = ROOT / "frontend" / "components" / "app-shell.tsx"


def test_legacy_dashboard_and_retired_strategy_launcher_are_removed():
    assert not LEGACY_WEB.exists()
    home = HOME.read_text(encoding="utf-8")
    shell = SHELL.read_text(encoding="utf-8")
    for retired in ("Cash-Future", "Calendar Spread", "Synthetic Arbitrage", "Paper Trading"):
        assert retired not in home
        assert retired not in shell
    assert 'const markets = ["NIFTY", "BANKNIFTY"]' in home
    assert "Box Spread" in home


def test_frontend_keeps_mobile_safe_layout_and_box_spread_workspace():
    home = HOME.read_text(encoding="utf-8")
    shell = SHELL.read_text(encoding="utf-8")
    assert "max-w-full" in home
    assert "/strategies/box-spread" in shell
    assert "box-spread-paper" not in shell
