from app.auto.live_paper import LivePaperTradeService
from app.models.global_paper_setting import GlobalPaperSetting


def _enable(db_session, user_id):
    db_session.add(
        GlobalPaperSetting(
            user_id=user_id,
            enabled=True,
            paper_amount=10_000_000,
            emergency_stop=False,
        )
    )
    db_session.commit()


def test_backend_integration_lifecycle_contract_is_user_scoped(db_session):
    svc = LivePaperTradeService()
    _enable(db_session, 1)
    _enable(db_session, 2)

    user_one, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="RELIANCE",
        event_id="CF-USER-1", direction="LONG", expiry="2026-10-30",
        lot_size=250, lots=1, edge=10, capital_used=100000, user_id=1,
    )
    assert created is True

    user_two, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="RELIANCE",
        event_id="CF-USER-2", direction="LONG", expiry="2026-10-30",
        lot_size=250, lots=1, edge=10, capital_used=100000, user_id=2,
    )
    assert created is True

    svc.mark(db_session, user_one, edge=14)
    svc.close(db_session, user_one, "MANUAL")

    assert [x.id for x in svc.ongoing(db_session, 1)] == []
    assert [x.id for x in svc.completed(db_session, 1)] == [user_one.id]
    assert [x.id for x in svc.ongoing(db_session, 2)] == [user_two.id]
    assert [x.id for x in svc.completed(db_session, 2)] == []
    assert user_one.realized_pnl == 1000.0
    assert user_two.realized_pnl == 0.0


def test_backend_integration_duplicate_signal_marks_existing_trade(db_session):
    svc = LivePaperTradeService()
    _enable(db_session, 1)

    first, created = svc.enter_or_mark(
        db_session, strategy_id="box-spread", symbol="NIFTY",
        event_id="BOX-DUP-1", direction="LONG", expiry="2026-10-30",
        lot_size=50, lots=1, edge=8, capital_used=100000, user_id=1,
    )
    assert created is True

    second, created = svc.enter_or_mark(
        db_session, strategy_id="box-spread", symbol="NIFTY",
        event_id="BOX-DUP-1", direction="LONG", expiry="2026-10-30",
        lot_size=50, lots=1, edge=11, capital_used=100000, user_id=1,
    )

    assert created is False
    assert second.id == first.id
    assert second.entry_edge == 8
    assert second.current_edge == 11
    assert len(svc.ongoing(db_session, 1)) == 1


def test_backend_integration_paper_gate_blocks_disabled_user(db_session):
    svc = LivePaperTradeService()
    db_session.add(
        GlobalPaperSetting(
            user_id=7, enabled=False, paper_amount=10_000_000, emergency_stop=False
        )
    )
    db_session.commit()

    trade, created = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="HDFCBANK",
        event_id="CAL-GATE-1", direction="LONG", expiry="2026-10-30",
        lot_size=550, lots=1, edge=5, capital_used=100000, user_id=7,
    )

    assert trade is None
    assert created is False
    assert svc.ongoing(db_session, 7) == []


def test_duplicate_signal_does_not_change_reserved_entry_capital(db_session):
    svc = LivePaperTradeService()
    _enable(db_session, 1)
    first, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="CAP-DUP-1", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=5, edge=5, capital_used=100000, user_id=1,
    )
    assert created is True
    second, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="CAP-DUP-1", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=5, edge=8, capital_used=25000, user_id=1,
    )
    assert created is False
    assert second.id == first.id
    assert second.capital_used == 100000
    assert second.current_edge == 8


def test_rule_driven_paper_entry_is_user_scoped(db_session):
    from app.models import AlertRule
    from app.notifications.common import AlertEvent, AlertService

    _enable(db_session, 1)
    _enable(db_session, 2)
    db_session.add_all([
        AlertRule(user_id=1, strategy_id="cash-future", min_gross_profit=0, mobile_number="111", whatsapp_enabled=True, enabled=True),
        AlertRule(user_id=2, strategy_id="cash-future", min_gross_profit=0, mobile_number="222", whatsapp_enabled=True, enabled=True),
    ])
    db_session.commit()

    class DummyNotifier:
        configured = True
        def send_text(self, mobile, message):
            return True

    event = AlertEvent(
        strategy_id="cash-future", event_id="RULE-USER-SCOPE",
        symbol="AAA", timestamp_ns=1, message="paper",
        metadata={"paper_trade": {
            "direction": "LONG", "expiry": "2026-10-30", "lot_size": 10,
            "lots": 1, "edge": 5, "capital_used": 100000,
        }},
    )
    assert AlertService(DummyNotifier()).dispatch(db_session, event) == 2
    assert len(LivePaperTradeService().ongoing(db_session, 1)) == 1
    assert len(LivePaperTradeService().ongoing(db_session, 2)) == 1


def test_alert_rule_position_limit_is_serialized_with_paper_allocation(db_session):
    from app.models import AlertRule
    from app.models.global_paper_setting import GlobalPaperSetting
    from app.notifications.common import AlertEvent, AlertService
    from datetime import datetime
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=1_000_000, emergency_stop=False))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_simultaneous_positions=1, max_daily_capital=1_000_000, max_loss=100_000,
    ))
    db_session.commit()
    service = AlertService()
    event1 = AlertEvent(
        strategy_id="cash-future", event_id="RISK-SERIAL-1", symbol="AAA",
        timestamp_ns=1, message="one", metadata={"gross_profit": 1000,
        "paper_trade": {"direction":"LONG", "expiry":"2026-10-30", "lot_size":10,
        "lots":1, "edge":5, "capital_used":100000}}, observed_at=datetime.utcnow(),
    )
    event2 = AlertEvent(
        strategy_id="cash-future", event_id="RISK-SERIAL-2", symbol="BBB",
        timestamp_ns=2, message="two", metadata={"gross_profit": 1000,
        "paper_trade": {"direction":"LONG", "expiry":"2026-10-30", "lot_size":10,
        "lots":1, "edge":5, "capital_used":100000}}, observed_at=datetime.utcnow(),
    )
    assert service.dispatch(db_session, event1) == 0
    assert service.dispatch(db_session, event2) == 0
    assert len(LivePaperTradeService().ongoing(db_session, 1)) == 1


def test_multiple_same_user_alert_rules_apply_strictest_limits(db_session):
    from app.models import AlertRule
    from app.models.global_paper_setting import GlobalPaperSetting
    from app.notifications.common import AlertEvent, AlertService
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=1_000_000, emergency_stop=False))
    db_session.add_all([
        AlertRule(user_id=1, strategy_id="cash-future", min_gross_profit=100,
                  mobile_number="", whatsapp_enabled=False, enabled=True,
                  max_simultaneous_positions=5, max_daily_capital=500000, max_loss=50000,
                  priority=10),
        AlertRule(user_id=1, strategy_id="cash-future", min_gross_profit=100,
                  mobile_number="", whatsapp_enabled=False, enabled=True,
                  max_simultaneous_positions=1, max_daily_capital=100000, max_loss=10000,
                  priority=1),
    ])
    db_session.commit()
    svc = LivePaperTradeService()
    first, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="PREEXISTING",
        event_id="MULTI-RULE-PRE", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=50000, user_id=1,
    )
    assert created is True

    event = AlertEvent(
        strategy_id="cash-future", event_id="MULTI-RULE-1", symbol="AAA", timestamp_ns=1,
        message="multi", metadata={"gross_profit": 200,
        "paper_trade": {"direction":"LONG", "expiry":"2026-10-30", "lot_size":10,
        "lots":1, "edge":5, "capital_used":50000}},
    )
    assert AlertService().dispatch(db_session, event) == 0
    assert len(svc.ongoing(db_session, 1)) == 1

    db_session.delete(first)
    db_session.commit()
    second = AlertService().dispatch(db_session, AlertEvent(
        strategy_id="cash-future", event_id="MULTI-RULE-2", symbol="BBB", timestamp_ns=2,
        message="multi2", metadata={"gross_profit": 200,
        "paper_trade": {"direction":"LONG", "expiry":"2026-10-30", "lot_size":10,
        "lots":1, "edge":5, "capital_used":120000}},
    ))
    assert second == 0
    rows = svc.ongoing(db_session, 1)
    assert len(rows) == 0


def test_alert_rule_threshold_and_limits_gate_paper_entry(db_session):
    from app.models import AlertRule
    from app.notifications.common import AlertEvent, AlertService

    _enable(db_session, 1)
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=100.0,
        mobile_number="111", whatsapp_enabled=True, enabled=True,
        max_daily_capital=100000.0, max_simultaneous_positions=1, max_loss=100.0,
    ))
    db_session.commit()

    class DummyNotifier:
        configured = True
        def send_text(self, mobile, message):
            return True

    service = AlertService(DummyNotifier())
    base = {
        "direction": "LONG", "expiry": "2026-10-30", "lot_size": 10,
        "lots": 1, "edge": 10, "capital_used": 60000,
    }

    low = AlertEvent(
        strategy_id="cash-future", event_id="RULE-GATE-LOW", symbol="AAA",
        timestamp_ns=1, message="low", metadata={"gross_profit": 50, "paper_trade": base},
    )
    assert service.dispatch(db_session, low) == 0
    assert len(LivePaperTradeService().ongoing(db_session, 1)) == 0

    first = AlertEvent(
        strategy_id="cash-future", event_id="RULE-GATE-1", symbol="AAA",
        timestamp_ns=2, message="first", metadata={"gross_profit": 200, "paper_trade": base},
    )
    assert service.dispatch(db_session, first) == 1
    assert len(LivePaperTradeService().ongoing(db_session, 1)) == 1

    second = AlertEvent(
        strategy_id="cash-future", event_id="RULE-GATE-2", symbol="BBB",
        timestamp_ns=3, message="second", metadata={"gross_profit": 200, "paper_trade": base},
    )
    assert service.dispatch(db_session, second) == 1
    assert len(LivePaperTradeService().ongoing(db_session, 1)) == 1


def test_alert_rule_daily_capital_and_loss_limits_gate_paper_entry(db_session):
    from app.models import AlertRule
    from app.notifications.common import AlertEvent, AlertService

    _enable(db_session, 1)
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0.0,
        mobile_number="111", whatsapp_enabled=False, enabled=True,
        max_daily_capital=100000.0, max_simultaneous_positions=5, max_loss=0.0,
    ))
    db_session.commit()

    class DummyNotifier:
        configured = False
        def send_text(self, mobile, message):
            return True

    service = AlertService(DummyNotifier())
    base = {
        "direction": "LONG", "expiry": "2026-10-30", "lot_size": 10,
        "lots": 1, "edge": 10, "capital_used": 60000,
    }

    first = AlertEvent(
        strategy_id="cash-future", event_id="DAILY-CAP-1", symbol="AAA",
        timestamp_ns=1, message="first", metadata={"gross_profit": 1, "paper_trade": base},
    )
    second = AlertEvent(
        strategy_id="cash-future", event_id="DAILY-CAP-2", symbol="BBB",
        timestamp_ns=2, message="second", metadata={"gross_profit": 1, "paper_trade": base},
    )
    service.dispatch(db_session, first)
    service.dispatch(db_session, second)
    assert [x.symbol for x in LivePaperTradeService().ongoing(db_session, 1)] == ["AAA"]

    _enable(db_session, 2)
    db_session.add(AlertRule(
        user_id=2, strategy_id="cash-future", min_gross_profit=0.0,
        mobile_number="222", whatsapp_enabled=False, enabled=True,
        max_daily_capital=0.0, max_simultaneous_positions=5, max_loss=100.0,
    ))
    db_session.commit()

    loss_base = {**base, "capital_used": 1000, "lot_size": 10}
    loss_event = AlertEvent(
        strategy_id="cash-future", event_id="LOSS-1", symbol="CCC",
        timestamp_ns=3, message="loss", metadata={"gross_profit": 1, "paper_trade": loss_base},
    )
    blocked_event = AlertEvent(
        strategy_id="cash-future", event_id="LOSS-2", symbol="DDD",
        timestamp_ns=4, message="blocked", metadata={"gross_profit": 1, "paper_trade": loss_base},
    )
    service.dispatch(db_session, loss_event)
    loss_trade = LivePaperTradeService().ongoing(db_session, 2)[0]
    LivePaperTradeService().mark(db_session, loss_trade, edge=0.0)
    service.dispatch(db_session, blocked_event)
    assert [x.symbol for x in LivePaperTradeService().ongoing(db_session, 2)] == ["CCC"]


def test_duplicate_alert_marks_existing_trade_even_when_risk_limit_is_reached(db_session):
    from app.models import AlertRule
    from app.notifications.common import AlertEvent, AlertService

    _enable(db_session, 1)
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0.0,
        mobile_number="111", whatsapp_enabled=False, enabled=True,
        max_daily_capital=1.0, max_simultaneous_positions=1, max_loss=1.0,
    ))
    db_session.commit()

    class DummyNotifier:
        configured = False
        def send_text(self, mobile, message):
            return True

    service = AlertService(DummyNotifier())
    base = {
        "direction": "LONG", "expiry": "2026-10-30", "lot_size": 10,
        "lots": 1, "edge": 10, "capital_used": 1000,
    }
    first = AlertEvent(
        strategy_id="cash-future", event_id="DUP-RISK-1", symbol="AAA",
        timestamp_ns=1, message="first", metadata={"gross_profit": 1, "paper_trade": base},
    )
    assert service.dispatch(db_session, first) == 0
    trade = LivePaperTradeService().ongoing(db_session, 1)[0]
    first_edge = trade.current_edge

    duplicate = AlertEvent(
        strategy_id="cash-future", event_id="DUP-RISK-1", symbol="AAA",
        timestamp_ns=2, message="duplicate", metadata={
            "gross_profit": 1,
            "paper_trade": {**base, "edge": 15, "capital_used": 999999},
        },
    )
    assert service.dispatch(db_session, duplicate) == 0
    trades = LivePaperTradeService().ongoing(db_session, 1)
    assert len(trades) == 1
    assert trades[0].current_edge == 15
    assert trades[0].capital_used == trade.capital_used
    assert trades[0].current_edge != first_edge


def test_expiry_close_releases_position_slot_but_keeps_realized_loss_limit(db_session):
    from datetime import datetime
    from app.models import AlertRule
    from app.notifications.common import AlertEvent, AlertService

    _enable(db_session, 1)
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0.0,
        mobile_number="111", whatsapp_enabled=False, enabled=True,
        max_daily_capital=0.0, max_simultaneous_positions=1, max_loss=100.0,
    ))
    db_session.commit()

    class DummyNotifier:
        configured = False
        def send_text(self, mobile, message):
            return True

    svc = LivePaperTradeService()
    service = AlertService(DummyNotifier())
    base = {
        "direction": "LONG", "expiry": "2026-10-10", "lot_size": 10,
        "lots": 1, "edge": 10, "capital_used": 1000,
    }
    first = AlertEvent(
        strategy_id="cash-future", event_id="EXPIRY-RISK-1", symbol="AAA",
        timestamp_ns=1, message="first", metadata={"gross_profit": 1, "paper_trade": base},
    )
    service.dispatch(db_session, first)
    trade = svc.ongoing(db_session, 1)[0]
    svc.mark(db_session, trade, edge=0.0)
    closed = svc.close_expired(db_session, now=datetime(2026, 10, 10, 15, 30))
    assert [x.id for x in closed] == [trade.id]
    assert svc.ongoing(db_session, 1) == []
    assert svc.completed(db_session, 1)[0].realized_pnl == -100.0

    second = AlertEvent(
        strategy_id="cash-future", event_id="EXPIRY-RISK-2", symbol="BBB",
        timestamp_ns=2, message="second", metadata={"gross_profit": 1, "paper_trade": base},
    )
    service.dispatch(db_session, second)
    assert svc.ongoing(db_session, 1) == []


def test_alert_risk_limits_use_ist_trading_day_boundary():
    from datetime import datetime, timezone
    from app.notifications.common import _ist_day_start_utc_naive

    now = datetime(2026, 10, 4, 18, 40, tzinfo=timezone.utc)
    assert _ist_day_start_utc_naive(now) == datetime(2026, 10, 4, 18, 30)


def test_completed_paper_event_cannot_reopen(db_session):
    svc = LivePaperTradeService()
    _enable(db_session, 1)
    first, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="REOPEN-GUARD-1", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=1, edge=5, capital_used=1000, user_id=1,
    )
    assert created is True
    svc.close(db_session, first, "MANUAL")

    reopened, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="REOPEN-GUARD-1", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=1, edge=9, capital_used=1000, user_id=1,
    )
    assert reopened is None
    assert created is False
    assert svc.ongoing(db_session, 1) == []
    assert [x.id for x in svc.completed(db_session, 1)] == [first.id]
