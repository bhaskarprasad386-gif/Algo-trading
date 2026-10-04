from datetime import datetime

from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _setup(db):
    db.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db.add(AlertRule(user_id=1, strategy_id="cash-future", min_gross_profit=100, enabled=True, max_daily_capital=0, max_loss=0, max_simultaneous_positions=5))
    db.commit()


def _event(event_id, gross):
    return AlertEvent(strategy_id="cash-future", event_id=event_id, symbol="AAA", timestamp_ns=1, message="paper", observed_at=datetime.utcnow(), metadata={
        "gross_profit": gross,
        "paper_trade": {"direction":"LONG","expiry":"2026-10-30","lot_size":10,"lots":1,"edge":5,"capital_used":30000,"legs":[]},
    })


def test_nonfinite_gross_profit_cannot_qualify_paper_entry(db_session):
    _setup(db_session)
    service = AlertService()
    for event_id, gross in [("NAN", float("nan")), ("INF", float("inf")), ("NEG-INF", float("-inf"))]:
        assert service.dispatch(db_session, _event(event_id, gross)) == 0
    assert db_session.query(LivePaperTrade).count() == 0


def test_valid_gross_profit_still_uses_rule_threshold(db_session):
    _setup(db_session)
    service = AlertService()
    assert service.dispatch(db_session, _event("BELOW", 99)) == 0
    assert db_session.query(LivePaperTrade).count() == 0
    assert service.dispatch(db_session, _event("ABOVE", 100)) == 0
    trade = db_session.query(LivePaperTrade).filter(LivePaperTrade.event_id == "ABOVE").one()
    assert trade.capital_used == 30000
