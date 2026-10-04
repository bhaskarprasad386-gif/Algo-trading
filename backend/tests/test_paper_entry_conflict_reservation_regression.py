"""Regression coverage for paper-entry conflict recovery and reservation integrity."""

from datetime import datetime
from sqlalchemy.exc import IntegrityError

from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting, LivePaperTrade


def _enable(db, user_id=1, amount=60000):
    db.add(GlobalPaperSetting(
        user_id=user_id,
        enabled=True,
        paper_amount=amount,
        emergency_stop=False,
    ))
    db.commit()


def _entry(db, *, user_id=1, event_id="RACE", strategy="cash-future",
           symbol="AAA", capital=60000, lots=2):
    return LivePaperTradeService().enter_or_mark(
        db,
        strategy_id=strategy,
        symbol=symbol,
        event_id=event_id,
        direction="LONG",
        expiry="2026-10-30",
        earliest_expiry="2026-10-30",
        lot_size=10,
        lots=lots,
        edge=10.0,
        capital_used=capital,
        legs=[{"side": "BUY", "price": 100.0}],
        metadata={"exchange": "NFO", "contract": event_id},
        user_id=user_id,
    )


def test_completed_event_replay_after_insert_conflict_never_reopens(db_session):
    _enable(db_session)
    original, created = _entry(db_session, event_id="COMPLETED-CONFLICT")
    assert created is True
    LivePaperTradeService().close(db_session, original, "MANUAL")

    replay, created = _entry(
        db_session,
        event_id="COMPLETED-CONFLICT",
        capital=120000,
        lots=4,
    )

    assert replay is None
    assert created is False
    rows = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "COMPLETED-CONFLICT",
    ).all()
    assert len(rows) == 1
    assert rows[0].status == "COMPLETED"
    assert rows[0].capital_used == 60000.0
    assert rows[0].lots == 2


def test_existing_malformed_ongoing_winner_is_not_marked_or_reallocated(db_session):
    _enable(db_session)
    original, created = _entry(db_session, event_id="MALFORMED-WINNER")
    assert created is True
    original.pnl_pct = 999.0
    db_session.commit()

    result, created = _entry(
        db_session,
        event_id="MALFORMED-WINNER",
        capital=30000,
        lots=1,
    )

    assert result is None
    assert created is False
    db_session.refresh(original)
    assert original.status == "ONGOING"
    assert original.pnl_pct == 999.0
    assert original.capital_used == 60000.0
    assert original.lots == 2


def test_invalid_winner_does_not_consume_available_capital_for_new_event(db_session):
    _enable(db_session, amount=60000)
    malformed, created = _entry(db_session, event_id="BAD-RESERVATION")
    assert created is True
    malformed.capital_used = float("nan")
    db_session.commit()

    result, created = _entry(
        db_session,
        event_id="NEW-AFTER-BAD",
        capital=30000,
        lots=1,
    )

    assert result is None
    assert created is False

    # The malformed row is fail-closed, so the service refuses to make a new
    # allocation rather than silently ignoring corrupted accounting data.
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.status == "ONGOING",
    ).count() == 1


def test_cross_user_same_event_conflict_remains_independent(db_session):
    _enable(db_session, user_id=1, amount=60000)
    _enable(db_session, user_id=2, amount=60000)

    user1, created1 = _entry(db_session, user_id=1, event_id="SAME-EVENT")
    user2, created2 = _entry(db_session, user_id=2, event_id="SAME-EVENT")
    assert created1 is True
    assert created2 is True

    service = LivePaperTradeService()
    marked1, created = _entry(
        db_session, user_id=1, event_id="SAME-EVENT",
        capital=120000, lots=4,
    )
    assert created is False
    assert marked1.id == user1.id

    db_session.refresh(user2)
    assert user2.status == "ONGOING"
    assert user2.capital_used == 60000.0
    assert user2.current_edge == 10.0


def test_conflicting_identity_cannot_mark_same_event_after_reservation_lock(db_session):
    _enable(db_session)
    original, created = _entry(
        db_session,
        event_id="IDENTITY-RACE",
        strategy="cash-future",
        symbol="AAA",
    )
    assert created is True

    wrong, created = _entry(
        db_session,
        event_id="IDENTITY-RACE",
        strategy="calendar-spread",
        symbol="BBB",
        capital=30000,
        lots=1,
    )

    assert wrong is None
    assert created is False
    db_session.refresh(original)
    assert original.strategy_id == "cash-future"
    assert original.symbol == "AAA"
    assert original.capital_used == 60000.0
    assert original.current_edge == 10.0


def test_manual_close_releases_ongoing_reservation_but_not_entry_identity(db_session):
    _enable(db_session, amount=60000)
    first, created = _entry(db_session, event_id="RELEASE-ONE")
    assert created is True
    LivePaperTradeService().close(db_session, first, "MANUAL")

    second, created = _entry(
        db_session,
        event_id="RELEASE-TWO",
        capital=60000,
        lots=2,
    )

    assert created is True
    assert second.capital_used == 60000.0
    assert second.lots == 2
    assert first.status == "COMPLETED"
    assert first.capital_used == 60000.0
