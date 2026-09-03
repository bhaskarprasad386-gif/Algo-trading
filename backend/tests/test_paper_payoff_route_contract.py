from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "app" / "execution" / "paper_routes.py"


def test_paper_payoff_route_uses_deterministic_engine():
    source = ROUTES.read_text(encoding="utf-8")
    assert '@router.post("/paper/payoff")' in source
    assert "PayoffLeg(" in source
    assert "payoff_summary(legs, prices)" in source
    assert '"mode": "paper"' in source


def test_paper_payoff_contract_separates_charges_from_analytics():
    source = ROUTES.read_text(encoding="utf-8")
    assert '"charges_status": "unavailable"' in source
    assert '"analytics": summary' in source


def test_paper_payoff_accepts_multi_leg_kinds_via_payoff_model():
    payoff = (ROOT / "app" / "execution" / "payoff.py").read_text(encoding="utf-8")
    for kind in ("SPOT", "FUTURE", "CALL", "PUT"):
        assert kind in payoff
