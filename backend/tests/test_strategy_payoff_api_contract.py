from pathlib import Path

PAPER_ROUTES = Path(__file__).resolve().parents[1] / "app" / "execution" / "paper_routes.py"


def test_strategy_payoff_route_uses_leg_builder():
    source = PAPER_ROUTES.read_text(encoding="utf-8")
    assert '@router.post("/paper/payoff/from-strategy")' in source
    assert "StrategyPayoffRequest" in source
    assert "StrategyLegInput" in source
    assert "build_strategy_legs" in source
    assert "payoff_summary(legs, prices)" in source


def test_cash_future_payoff_route_uses_scanner_strategy_builder():
    source = PAPER_ROUTES.read_text(encoding="utf-8")
    assert '@router.post("/paper/payoff/from-cash-future")' in source
    assert "CashFuturePayoffRequest" in source
    assert "build_cash_future_strategy" in source


def test_payoff_api_keeps_charges_explicitly_unavailable():
    source = PAPER_ROUTES.read_text(encoding="utf-8")
    assert '"charges_status":"unavailable"' in source
