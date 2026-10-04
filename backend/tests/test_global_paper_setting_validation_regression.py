from datetime import datetime

from app.models import GlobalPaperSetting, LivePaperTrade
from app.auto.live_paper import LivePaperTradeService


def _trade_payload(event_id):
    return dict(strategy_id="cash-future", symbol="AAA", event_id=event_id, direction="LONG",
                expiry="2026-10-30", earliest_expiry="2026-10-30", lot_size=10, lots=1,
                edge=5, capital_used=30000, legs=[], metadata={})


def test_malformed_global_paper_amount_fails_closed(db_session):
    service = LivePaperTradeService()
    bad_values = [float("nan"), float("inf"), float("-inf"), -1.0]
    for i, amount in enumerate(bad_values):
        db_session.add(GlobalPaperSetting(user_id=i + 1, enabled=True, paper_amount=amount, emergency_stop=False))
        db_session.commit()
        trade, created = service.enter_or_mark(db_session, user_id=i + 1, **_trade_payload(f"BAD-{i}"))
        assert trade is None
        assert created is False
        assert db_session.query(LivePaperTrade).filter(LivePaperTrade.user_id == i + 1).count() == 0


def test_valid_positive_global_paper_amount_still_allocates(db_session):
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=50000, emergency_stop=False))
    db_session.commit()
    trade, created = LivePaperTradeService().enter_or_mark(db_session, user_id=1, **_trade_payload("VALID"))
    assert created is True
    assert trade is not None
    assert trade.capital_used == 30000
