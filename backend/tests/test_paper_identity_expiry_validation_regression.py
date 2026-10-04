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



def test_duplicate_event_rejects_cross_strategy_and_cross_symbol_mark(db_session):
    _setup(db_session)
    svc = LivePaperTradeService()
    seed, created = _enter(
        db_session, event_id="IDENTITY-GUARD", strategy_id="cash-future",
        symbol="AAA", direction="LONG", expiry="2026-10-30",
    )
    assert created is True

    wrong_strategy, wrong_strategy_created = _enter(
        db_session, event_id="IDENTITY-GUARD", strategy_id="calendar-spread",
        symbol="AAA", direction="LONG", expiry="2026-10-30",
    )
    assert wrong_strategy is None
    assert wrong_strategy_created is False

    wrong_symbol, wrong_symbol_created = _enter(
        db_session, event_id="IDENTITY-GUARD", strategy_id="cash-future",
        symbol="BBB", direction="LONG", expiry="2026-10-30",
    )
    assert wrong_symbol is None
    assert wrong_symbol_created is False

    rows = svc.ongoing(db_session, 1)
    assert len(rows) == 1
    assert rows[0].strategy_id == "cash-future"
    assert rows[0].symbol == "AAA"
    assert rows[0].entry_edge == 5
    assert rows[0].current_edge == 5


def test_duplicate_event_direction_and_expiry_are_immutable(db_session):
    _setup(db_session)
    svc = LivePaperTradeService()
    seed, created = _enter(
        db_session, event_id="IDENTITY-IMMUTABLE",
        strategy_id="cash-future", symbol="AAA",
        direction="LONG", expiry="2026-10-30",
    )
    assert created is True
    original_lot_size = seed.lot_size
    original_lots = seed.lots
    original_capital = seed.capital_used
    original_entry_edge = seed.entry_edge

    duplicate, duplicate_created = _enter(
        db_session, event_id="IDENTITY-IMMUTABLE",
        strategy_id="cash-future", symbol="AAA",
        direction="SHORT", expiry="2026-11-30",
    )
    assert duplicate_created is False
    assert duplicate is not None

    row = svc.ongoing(db_session, 1)[0]
    assert row.id == seed.id
    assert row.direction == "LONG"
    assert row.expiry == "2026-10-30"
    assert row.earliest_expiry == "2026-10-30"
    assert row.lot_size == original_lot_size
    assert row.lots == original_lots
    assert row.capital_used == original_capital
    assert row.entry_edge == original_entry_edge
