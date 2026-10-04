from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule, GlobalPaperSetting
from app.notifications.common import AlertEvent, AlertService


def _enable(db, user_id, amount=200_000):
    db.add(GlobalPaperSetting(
        user_id=user_id, enabled=True, paper_amount=amount, emergency_stop=False,
    ))
    db.commit()


def test_user_risk_limits_do_not_cross_user_for_same_alert_event(db_session):
    _enable(db_session, 1)
    _enable(db_session, 2)
    db_session.add_all([
        AlertRule(
            user_id=1, strategy_id="cash-future", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_simultaneous_positions=1, max_daily_capital=50_000, max_loss=1,
        ),
        AlertRule(
            user_id=2, strategy_id="cash-future", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_simultaneous_positions=5, max_daily_capital=200_000, max_loss=10_000,
        ),
    ])
    db_session.commit()

    svc = LivePaperTradeService()
    seed, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="SEED",
        event_id="USER-RISK-SEED", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=1, edge=10, capital_used=50_000, user_id=1,
    )
    assert created is True
    svc.mark(db_session, seed, edge=9)

    event = AlertEvent(
        strategy_id="cash-future", event_id="USER-RISK-SHARED",
        symbol="AAA", timestamp_ns=1, message="shared",
        metadata={
            "gross_profit": 100,
            "paper_trade": {
                "direction": "LONG", "expiry": "2026-10-30",
                "lot_size": 10, "lots": 1, "edge": 5,
                "capital_used": 50_000,
            },
        },
    )
    assert AlertService().dispatch(db_session, event) == 0

    user_one = svc.ongoing(db_session, 1)
    user_two = svc.ongoing(db_session, 2)
    assert [row.symbol for row in user_one] == ["SEED"]
    assert [row.symbol for row in user_two] == ["AAA"]
    assert user_two[0].user_id == 2


def test_same_event_id_isolated_by_user_and_duplicate_mark_stays_user_scoped(db_session):
    _enable(db_session, 1)
    _enable(db_session, 2)
    db_session.add_all([
        AlertRule(user_id=1, strategy_id="cash-future", min_gross_profit=0,
                  mobile_number="", whatsapp_enabled=False, enabled=True),
        AlertRule(user_id=2, strategy_id="cash-future", min_gross_profit=0,
                  mobile_number="", whatsapp_enabled=False, enabled=True),
    ])
    db_session.commit()

    svc = LivePaperTradeService()
    event = AlertEvent(
        strategy_id="cash-future", event_id="SAME-EVENT-USERS",
        symbol="AAA", timestamp_ns=1, message="same",
        metadata={
            "gross_profit": 10,
            "paper_trade": {
                "direction": "LONG", "expiry": "2026-10-30",
                "lot_size": 10, "lots": 1, "edge": 5,
                "capital_used": 20_000,
            },
        },
    )
    assert AlertService().dispatch(db_session, event) == 0

    first = svc.ongoing(db_session, 1)[0]
    second = svc.ongoing(db_session, 2)[0]
    assert first.id != second.id
    assert first.user_id == 1
    assert second.user_id == 2

    duplicate = AlertEvent(
        strategy_id="cash-future", event_id="SAME-EVENT-USERS",
        symbol="AAA", timestamp_ns=2, message="duplicate",
        metadata={
            "gross_profit": 10,
            "paper_trade": {
                "direction": "LONG", "expiry": "2026-10-30",
                "lot_size": 10, "lots": 1, "edge": 11,
                "capital_used": 999_999,
            },
        },
    )
    assert AlertService().dispatch(db_session, duplicate) == 0
    assert svc.ongoing(db_session, 1)[0].current_edge == 11
    assert svc.ongoing(db_session, 2)[0].current_edge == 11
    assert svc.ongoing(db_session, 1)[0].capital_used == 20_000
    assert svc.ongoing(db_session, 2)[0].capital_used == 20_000


def test_user_mark_survives_later_users_risk_rollback(db_session):
    _enable(db_session, 1)
    _enable(db_session, 2)
    db_session.add_all([
        AlertRule(
            user_id=1, strategy_id="cash-future", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_simultaneous_positions=5, max_daily_capital=200_000, max_loss=10_000,
        ),
        AlertRule(
            user_id=2, strategy_id="cash-future", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_simultaneous_positions=5, max_daily_capital=200_000, max_loss=100,
        ),
    ])
    db_session.commit()

    svc = LivePaperTradeService()
    user_one_trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="CROSS-USER-MARK", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=20_000, user_id=1,
    )
    assert created is True

    user_two_seed, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="SEED2",
        event_id="CROSS-USER-LOSS", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=20_000, user_id=2,
    )
    assert created is True
    svc.mark(db_session, user_two_seed, edge=4, pnl_override=-100.0)
    db_session.commit()

    event = AlertEvent(
        strategy_id="cash-future", event_id="CROSS-USER-MARK",
        symbol="AAA", timestamp_ns=2, message="shared",
        metadata={
            "gross_profit": 10,
            "paper_trade": {
                "direction": "LONG", "expiry": "2026-10-30",
                "lot_size": 10, "lots": 1, "edge": 11,
                "capital_used": 999_999,
            },
        },
    )

    assert AlertService().dispatch(db_session, event) == 0

    # User 1's duplicate mark must survive user 2's later max-loss rollback.
    refreshed_one = svc.ongoing(db_session, 1)[0]
    assert refreshed_one.current_edge == 11
    assert refreshed_one.unrealized_pnl == 60.0

    refreshed_two = svc.ongoing(db_session, 2)[0]
    assert refreshed_two.event_id == "CROSS-USER-LOSS"
    assert refreshed_two.unrealized_pnl == -100.0
