import threading
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
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



def test_concurrent_same_event_and_conflicting_identity_creates_only_one_correct_trade(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'identity-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    setup.commit()
    setup.close()

    barrier = threading.Barrier(3)
    results = []
    errors = []

    def worker(strategy_id, symbol, edge):
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade, created = LivePaperTradeService().enter_or_mark(
                db,
                strategy_id=strategy_id,
                symbol=symbol,
                event_id="IDENTITY-RACE",
                direction="LONG",
                expiry="2026-10-30",
                earliest_expiry="2026-10-30",
                lot_size=10,
                lots=1,
                edge=edge,
                capital_used=30000,
                user_id=1,
            )
            results.append((strategy_id, symbol, created, None if trade is None else trade.id))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=("cash-future", "AAA", 10)),
        threading.Thread(target=worker, args=("cash-future", "AAA", 12)),
        threading.Thread(target=worker, args=("calendar-spread", "BBB", 99)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 3

    verify = Session()
    rows = verify.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "IDENTITY-RACE",
    ).all()
    assert len(rows) == 1
    winner = rows[0]
    assert winner.strategy_id in {"cash-future", "calendar-spread"}
    assert winner.symbol in {"AAA", "BBB"}
    assert winner.capital_used == 30000
    assert winner.lots == 1

    for strategy_id, symbol, created, trade_id in results:
        if strategy_id == winner.strategy_id and symbol == winner.symbol:
            assert trade_id == winner.id
            # The matching duplicate may mark the winner; it must never create
            # a second allocation.
            assert created in {True, False}
        else:
            assert trade_id is None
            assert created is False

    verify.close()



def test_concurrent_same_event_across_users_is_fully_isolated(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cross-user-event-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add_all([
        GlobalPaperSetting(user_id=1, enabled=True, paper_amount=60000, emergency_stop=False),
        GlobalPaperSetting(user_id=2, enabled=True, paper_amount=60000, emergency_stop=False),
    ])
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def worker(user_id, symbol, edge):
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade, created = LivePaperTradeService().enter_or_mark(
                db,
                strategy_id="cash-future",
                symbol=symbol,
                event_id="SAME-EVENT-CROSS-USER",
                direction="LONG",
                expiry="2026-10-30",
                earliest_expiry="2026-10-30",
                lot_size=10,
                lots=2,
                edge=edge,
                capital_used=60000,
                user_id=user_id,
            )
            results.append((user_id, created, None if trade is None else trade.id))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=(1, "USER1-SYMBOL", 10)),
        threading.Thread(target=worker, args=(2, "USER2-SYMBOL", 20)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2
    assert {row[0] for row in results} == {1, 2}
    assert all(row[1] is True for row in results)

    verify = Session()
    rows = verify.query(LivePaperTrade).filter(
        LivePaperTrade.event_id == "SAME-EVENT-CROSS-USER",
    ).all()
    assert len(rows) == 2
    by_user = {row.user_id: row for row in rows}

    assert set(by_user) == {1, 2}
    assert by_user[1].symbol == "USER1-SYMBOL"
    assert by_user[2].symbol == "USER2-SYMBOL"
    assert by_user[1].capital_used == 60000
    assert by_user[2].capital_used == 60000
    assert by_user[1].lots == 2
    assert by_user[2].lots == 2
    assert by_user[1].current_edge == 10
    assert by_user[2].current_edge == 20

    # Each user's reservation is isolated even though event_id is identical.
    for user_id in (1, 2):
        user_rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == user_id,
            LivePaperTrade.status == "ONGOING",
        ).all()
        assert len(user_rows) == 1
        assert sum(float(row.capital_used) for row in user_rows) == 60000

    verify.close()
