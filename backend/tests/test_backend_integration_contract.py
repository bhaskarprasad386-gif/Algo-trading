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
