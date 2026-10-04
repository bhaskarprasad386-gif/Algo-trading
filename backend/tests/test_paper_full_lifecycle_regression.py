from datetime import datetime
from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting


def _enable(db, user_id=1, amount=100_000):
    db.add(GlobalPaperSetting(
        user_id=user_id, enabled=True, paper_amount=amount, emergency_stop=False,
    ))
    db.commit()


def test_full_paper_lifecycle_does_not_reopen_or_reallocate_completed_event(db_session):
    _enable(db_session)

    svc = LivePaperTradeService()
    trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="LIFECYCLE-1", direction="LONG", expiry="2026-10-04",
        lot_size=10, lots=2, edge=10, capital_used=60_000, user_id=1,
    )
    assert created is True
    assert trade.lots == 2
    assert trade.capital_used == 60_000

    duplicate, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="LIFECYCLE-1", direction="LONG", expiry="2026-10-04",
        lot_size=10, lots=2, edge=7, capital_used=999_999, user_id=1,
    )
    assert created is False
    assert duplicate.id == trade.id
    assert duplicate.capital_used == 60_000
    assert duplicate.current_edge == 7
    # Direction is immutable for the lifetime of the paper position. A
    # duplicate signal with the opposite direction must remain mark-only and
    # must not flip the persisted trade side.
    assert duplicate.direction == "LONG"
    opposite, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="LIFECYCLE-1", direction="SHORT", expiry="2026-10-04",
        lot_size=10, lots=2, edge=6, capital_used=999_999, user_id=1,
    )
    assert created is False
    assert opposite.id == trade.id
    assert opposite.direction == "LONG"
    assert opposite.current_edge == 6

    # Duplicate signals must not rewrite either expiry boundary of the
    # existing position. The earliest expiry is the lifecycle close boundary.
    db_session.refresh(opposite)
    assert opposite.expiry == "2026-10-04"
    assert opposite.earliest_expiry == "2026-10-04"

    # Executable legs and exchange metadata are entry-time identity. A
    # duplicate signal with a different contract payload must remain mark-only.
    original_legs = '[{"price":100.0,"side":"BUY"}]'
    original_metadata = '{"exchange":"NFO","contract":"ORIGINAL"}'
    persisted = db_session.query(type(trade)).filter(
        type(trade).id == trade.id
    ).one()
    persisted.legs_json = original_legs
    persisted.metadata_json = original_metadata
    db_session.commit()

    duplicate_payload, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="LIFECYCLE-1", direction="LONG", expiry="2026-12-31",
        earliest_expiry="2026-12-30", lot_size=99, lots=99, edge=5,
        capital_used=1_000_000,
        legs=[{"price": 999.0, "side": "SELL"}],
        metadata={"exchange": "MCX", "contract": "MALICIOUS-REPLACE"},
        user_id=1,
    )
    assert created is False
    assert duplicate_payload.id == trade.id
    db_session.refresh(duplicate_payload)
    assert duplicate_payload.legs_json == original_legs
    assert duplicate_payload.metadata_json == original_metadata

    svc.mark(db_session, trade, edge=7)
    assert trade.unrealized_pnl == -60.0
    assert trade.pnl_pct == -0.1

    closed = svc.close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
    )
    assert [x.id for x in closed] == [trade.id]

    completed = svc.completed(db_session, 1)
    assert [x.id for x in completed] == [trade.id]
    assert completed[0].status == "COMPLETED"
    assert completed[0].exit_reason == "EXPIRY_CLOSE"
    assert completed[0].realized_pnl == -60.0
    # Allocation identity is immutable through mark/close.
    assert completed[0].lot_size == 10
    assert completed[0].lots == 2
    assert completed[0].unrealized_pnl == -60.0
    assert completed[0].pnl_pct == -0.1
    assert completed[0].closed_at == completed[0].last_mark_at

    replay, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="LIFECYCLE-1", direction="LONG", expiry="2026-10-04",
        lot_size=10, lots=2, edge=20, capital_used=60_000, user_id=1,
    )
    assert replay is None
    assert created is False
    assert svc.ongoing(db_session, 1) == []
    assert [x.id for x in svc.completed(db_session, 1)] == [trade.id]
