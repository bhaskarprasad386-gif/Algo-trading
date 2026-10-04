from datetime import datetime

from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _setup(db):
    db.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db.add(AlertRule(user_id=1, strategy_id="cash-future", min_gross_profit=0, enabled=True, max_daily_capital=0, max_loss=0, max_simultaneous_positions=5))
    db.commit()


def _event(event_id, *, lot_size=10, lots=1, edge=5, capital=30000):
    return AlertEvent(
        strategy_id="cash-future", event_id=event_id, symbol="AAA", timestamp_ns=1,
        message="paper", observed_at=datetime.utcnow(),
        metadata={"gross_profit": 1000, "paper_trade": {
            "direction": "LONG", "expiry": "2026-10-30", "lot_size": lot_size,
            "lots": lots, "edge": edge, "capital_used": capital, "legs": []
        }},
    )


def test_alert_rejects_fractional_nonfinite_or_nonpositive_paper_numerics(db_session):
    _setup(db_session)
    service = AlertService()
    cases = [
        ("FRACTIONAL-LOTS", 10, 1.5, 5, 30000),
        ("FRACTIONAL-LOT-SIZE", 10.5, 1, 5, 30000),
        ("NAN-EDGE", 10, 1, float("nan"), 30000),
        ("INF-CAPITAL", 10, 1, 5, float("inf")),
        ("ZERO-CAPITAL", 10, 1, 5, 0),
        ("NEGATIVE-EDGE", 10, 1, -1, 30000),
    ]
    for case in cases:
        assert service.dispatch(db_session, _event(case[0], lot_size=case[1], lots=case[2], edge=case[3], capital=case[4])) == 0
    assert db_session.query(LivePaperTrade).count() == 0
