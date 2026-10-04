from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting, LivePaperTrade


def test_non_finite_paper_entry_inputs_are_rejected(db_session):
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db_session.commit()
    svc = LivePaperTradeService()
    for event_id, edge, capital, lot_size, lots in [
        ("NAN-EDGE", float("nan"), 30000, 10, 1),
        ("INF-CAPITAL", 5, float("inf"), 10, 1),
        ("FRACTIONAL-LOTS", 5, 30000, 10, 1.5),
        ("FRACTIONAL-LOT-SIZE", 5, 30000, 10.5, 1),
    ]:
        trade, created = svc.enter_or_mark(db_session, strategy_id="cash-future", symbol="VALIDATION", event_id=event_id, direction="LONG", expiry="2026-10-30", lot_size=lot_size, lots=lots, edge=edge, capital_used=capital, user_id=1)
        assert trade is None
        assert created is False
    assert db_session.query(LivePaperTrade).count() == 0
