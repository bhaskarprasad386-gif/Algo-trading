from datetime import datetime

from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _event(event_id):
    return AlertEvent(strategy_id="cash-future", event_id=event_id, symbol="AAA", timestamp_ns=1, message="paper", observed_at=datetime.utcnow(), metadata={
        "gross_profit": 1000,
        "paper_trade": {"direction":"LONG","expiry":"2026-10-30","lot_size":10,"lots":1,"edge":5,"capital_used":30000,"legs":[]},
    })


def _setting(db):
    db.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db.commit()


def test_malformed_persisted_risk_limits_fail_closed(db_session):
    _setting(db_session)
    bad = [
        dict(min_gross_profit=float("nan")),
        dict(max_loss=float("nan")),
        dict(max_daily_capital=float("inf")),
        dict(max_loss=-1),
        dict(max_daily_capital=-1),
        dict(max_simultaneous_positions=0),
        dict(cooldown_seconds=float("inf")),
    ]
    for i, values in enumerate(bad):
        rule = AlertRule(user_id=1, strategy_id="cash-future", mobile_number="", whatsapp_enabled=False, enabled=True,
                         min_gross_profit=values.pop("min_gross_profit", 0), max_loss=values.pop("max_loss", 10000), max_daily_capital=values.pop("max_daily_capital", 100000), max_simultaneous_positions=values.pop("max_simultaneous_positions", 5),
                         cooldown_seconds=values.pop("cooldown_seconds", 60), **values)
        db_session.add(rule)
        try:
            db_session.commit()
        except Exception:
            db_session.rollback()
            continue
        assert AlertService().dispatch(db_session, _event(f"BAD-{i}")) == 0
        assert db_session.query(LivePaperTrade).filter(LivePaperTrade.event_id == f"BAD-{i}").count() == 0
        db_session.delete(rule)
        db_session.commit()


def test_valid_zero_risk_limits_remain_unlimited(db_session):
    _setting(db_session)
    db_session.add(AlertRule(user_id=1, strategy_id="cash-future", mobile_number="", whatsapp_enabled=False, enabled=True,
                             min_gross_profit=0, max_loss=0, max_daily_capital=0, max_simultaneous_positions=5, cooldown_seconds=0))
    db_session.commit()
    assert AlertService().dispatch(db_session, _event("VALID")) == 0
    assert db_session.query(LivePaperTrade).filter(LivePaperTrade.event_id == "VALID").count() == 1
