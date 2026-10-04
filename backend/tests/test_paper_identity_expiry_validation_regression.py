from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting, LivePaperTrade


def _setup(db):
    db.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db.commit()


def _enter(db, event_id="VALID", strategy_id="cash-future", symbol="AAA", direction="LONG", expiry="2026-10-30", earliest_expiry=None):
    return LivePaperTradeService().enter_or_mark(
        db, strategy_id=strategy_id, symbol=symbol, event_id=event_id,
        direction=direction, expiry=expiry, earliest_expiry=earliest_expiry,
        lot_size=10, lots=1, edge=5, capital_used=30000, user_id=1,
    )


def test_malformed_identity_and_expiry_cannot_create_paper_trade(db_session):
    _setup(db_session)
    cases = [
        {"event_id": "", "symbol": "AAA"},
        {"event_id": "EMPTY-SYMBOL", "symbol": ""},
        {"event_id": "BAD-DIRECTION", "direction": "BUY"},
        {"event_id": "MISSING-EXPIRY", "expiry": None},
        {"event_id": "BAD-EXPIRY", "expiry": "not-a-date"},
        {"event_id": "BAD-EARLIEST", "earliest_expiry": "not-a-date"},
    ]
    for case in cases:
        trade, created = _enter(db_session, **case)
        assert trade is None
        assert created is False
    assert db_session.query(LivePaperTrade).count() == 0


def test_valid_long_short_and_calendar_earliest_expiry_still_work(db_session):
    _setup(db_session)
    long_trade, created = _enter(db_session, event_id="VALID-LONG", direction="long")
    assert created is True
    assert long_trade.direction == "LONG"
    short_trade, created = _enter(db_session, event_id="VALID-SHORT", direction="short")
    assert created is True
    assert short_trade.direction == "SHORT"
    calendar_trade, created = _enter(
        db_session, event_id="VALID-CALENDAR", expiry="2026-10-30", earliest_expiry="2026-10-12"
    )
    assert created is True
    assert calendar_trade.earliest_expiry == "2026-10-12"
