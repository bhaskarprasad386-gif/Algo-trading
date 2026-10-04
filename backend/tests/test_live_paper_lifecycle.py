from app.core.database import Base
from datetime import datetime, time
from app.auto.live_paper import LivePaperTradeService
from app.models.live_paper_trade import LivePaperTrade
from app.models.global_paper_setting import GlobalPaperSetting

def test_live_paper_trade_lifecycle_and_ranking(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=10_000_000, emergency_stop=False))
    db_session.commit()
    a,_ = svc.enter_or_mark(db_session, strategy_id="calendar-spread", symbol="ABC",
        event_id="A", direction="LONG", expiry="2026-10-10", earliest_expiry="2026-10-10",
        lot_size=100, lots=1, edge=5, capital_used=100000, legs=[{"side":"BUY","symbol":"ABC"}])
    b,_ = svc.enter_or_mark(db_session, strategy_id="box-spread", symbol="XYZ",
        event_id="B", direction="LONG", expiry="2026-10-30", earliest_expiry="2026-10-30",
        lot_size=10, lots=1, edge=20, capital_used=100000)
    svc.mark(db_session, a, edge=7)
    svc.mark(db_session, b, edge=10)
    db_session.commit()
    rows=svc.ongoing(db_session)
    assert [x.id for x in rows] == [a.id, b.id]
    assert rows[0].unrealized_pnl == 200.0
    svc.close(db_session, a, "MANUAL")
    assert a.status == "COMPLETED"
    assert svc.completed(db_session)[0].id == a.id

def test_calendar_earlier_expiry_closes_both_as_one_trade(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=10_000_000, emergency_stop=False))
    db_session.commit()
    trade,_ = svc.enter_or_mark(db_session, strategy_id="calendar-spread", symbol="NIFTY",
        event_id="CAL-1", direction="LONG", expiry="2026-10-30", earliest_expiry="2026-10-10",
        lot_size=50, lots=1, edge=4, capital_used=100000)
    closed = svc.close_expired(db_session, now=datetime(2026,10,10,15,30))
    assert len(closed) == 1
    assert closed[0].status == "COMPLETED"
    assert closed[0].exit_reason == "EXPIRY_CLOSE"


def test_numeric_expiry_closes_synthetic_trade(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=10_000_000, emergency_stop=False))
    db_session.commit()
    trade,_ = svc.enter_or_mark(db_session, strategy_id="synthetic-future-cash-carry", symbol="NIFTY",
        event_id="SYN-1", direction="LONG", expiry="20261010", earliest_expiry="20261010",
        lot_size=50, lots=1, edge=4, capital_used=100000)
    closed = svc.close_expired(db_session, now=datetime(2026,10,10,15,30))
    assert len(closed) == 1
    assert closed[0].exit_reason == "EXPIRY_CLOSE"


def _paper_trade(legs, lot_size=10, lots=2):
    import json
    return LivePaperTrade(lot_size=lot_size, lots=lots, legs_json=json.dumps(legs))


def test_executable_pnl_scales_with_reduced_partial_lots():
    from app.main import _executable_paper_pnl
    trade = _paper_trade([
        {"instrument": "CASH", "side": "BUY", "price": 100.0},
        {"instrument": "FUTURE", "side": "SELL", "price": 105.0},
    ], lot_size=10, lots=1)
    row = {"cash_bid": 103.0, "cash_ask": 104.0, "future_bid": 102.0, "future_ask": 103.0}
    # Per-lot executable P&L is 10 points; one allocated lot means 10 * lot_size.
    assert _executable_paper_pnl(trade, row) == 100.0


def test_partial_lots_mark_and_close_scale_pnl_and_pnl_pct(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=125000, emergency_stop=False))
    db_session.commit()

    seed, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="SEED",
        event_id="PARTIAL-PNL-SEED", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=3, edge=5, capital_used=90000, user_id=1,
    )
    assert created is True

    trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="PARTIAL-PNL-1", direction="LONG", expiry="2026-10-30",
        lot_size=10, lots=2, edge=10, capital_used=60000, user_id=1,
    )
    assert created is True
    assert trade.lots == 1
    assert trade.capital_used == 30000

    svc.mark(db_session, trade, edge=20)
    assert trade.unrealized_pnl == 100.0
    assert trade.pnl_pct == round(100.0 / 30000.0 * 100.0, 8)

    svc.close(db_session, trade, "MANUAL")
    assert trade.realized_pnl == 100.0
    assert trade.lots == 1
    assert trade.capital_used == 30000


def test_executable_pnl_cash_future_uses_exit_bid_ask():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace
    trade = _paper_trade([
        {"instrument": "CASH", "side": "BUY", "price": 100.0},
        {"instrument": "FUTURE", "side": "SELL", "price": 105.0},
    ])
    row = {"cash_bid": 103.0, "cash_ask": 104.0, "future_bid": 102.0, "future_ask": 103.0}
    assert _executable_paper_pnl(trade, row) == 100.0


def test_executable_pnl_synthetic_uses_nested_quotes():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace
    trade = _paper_trade([
        {"instrument": "FUTURE", "side": "BUY", "price": 100.0},
        {"instrument": "CALL", "side": "BUY", "price": 10.0},
        {"instrument": "PUT", "side": "SELL", "price": 8.0},
    ])
    option = SimpleNamespace(call_bid=12.0, call_ask=13.0, put_bid=7.0, put_ask=9.0)
    future = SimpleNamespace(bid=102.0, ask=103.0)
    row = SimpleNamespace(option=option, future=future)
    assert _executable_paper_pnl(trade, row) == 40.0


def test_executable_pnl_box_uses_nested_low_high_quotes():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace
    trade = _paper_trade([
        {"instrument": "LOW_CALL", "side": "BUY", "price": 10.0},
        {"instrument": "LOW_PUT", "side": "BUY", "price": 9.0},
        {"instrument": "HIGH_CALL", "side": "SELL", "price": 5.0},
        {"instrument": "HIGH_PUT", "side": "SELL", "price": 4.0},
    ])
    low = SimpleNamespace(call_bid=11.0, call_ask=12.0, put_bid=10.0, put_ask=11.0)
    high = SimpleNamespace(call_bid=3.0, call_ask=4.0, put_bid=2.0, put_ask=3.0)
    row = SimpleNamespace(low=low, high=high)
    assert _executable_paper_pnl(trade, row) == 40.0


def test_executable_pnl_calendar_uses_contract_specific_quotes():
    from app.main import _executable_paper_pnl
    trade = _paper_trade([
        {"contract": "NEAR", "side": "BUY", "price": 100.0},
        {"contract": "FAR", "side": "SELL", "price": 110.0},
    ])
    from types import SimpleNamespace
    row = SimpleNamespace(near_contract_month="NEAR", far_contract_month="FAR",
                          near_bid=104.0, near_ask=105.0, far_bid=106.0, far_ask=107.0)
    assert _executable_paper_pnl(trade, row) == 140.0




def test_executable_pnl_calendar_normalized_long_and_short_directions():
    """Calendar ledger direction is normalized, but persisted leg sides own P&L sign."""
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace

    row = SimpleNamespace(
        near_contract_month="NEAR", far_contract_month="FAR",
        near_bid=103.0, near_ask=104.0,
        far_bid=108.0, far_ask=109.0,
    )

    # LONG_NEAR_SHORT_FAR -> ledger LONG; BUY near at 100, SELL far at 110.
    long_trade = _paper_trade([
        {"contract": "NEAR", "side": "BUY", "price": 100.0},
        {"contract": "FAR", "side": "SELL", "price": 110.0},
    ])
    assert _executable_paper_pnl(long_trade, row) == 2.0

    # SHORT_NEAR_LONG_FAR -> ledger SHORT; SELL near at 110, BUY far at 100.
    short_trade = _paper_trade([
        {"contract": "NEAR", "side": "SELL", "price": 110.0},
        {"contract": "FAR", "side": "BUY", "price": 100.0},
    ])
    assert _executable_paper_pnl(short_trade, row) == 2.0


def test_executable_pnl_synthetic_normalized_long_and_short_directions():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace

    row = SimpleNamespace(
        option=SimpleNamespace(
            call_bid=12.0, call_ask=13.0,
            put_bid=7.0, put_ask=8.0,
        ),
        future=SimpleNamespace(bid=102.0, ask=103.0),
    )

    long_trade = _paper_trade([
        {"instrument": "FUTURE", "side": "BUY", "price": 100.0},
        {"instrument": "CALL", "side": "BUY", "price": 10.0},
        {"instrument": "PUT", "side": "SELL", "price": 8.0},
    ])
    assert _executable_paper_pnl(long_trade, row) == 4.0

    short_trade = _paper_trade([
        {"instrument": "FUTURE", "side": "SELL", "price": 100.0},
        {"instrument": "CALL", "side": "SELL", "price": 10.0},
        {"instrument": "PUT", "side": "BUY", "price": 8.0},
    ])
    assert _executable_paper_pnl(short_trade, row) == 4.0


def test_executable_pnl_box_normalized_long_and_short_directions():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace

    row = SimpleNamespace(
        low=SimpleNamespace(
            call_bid=11.0, call_ask=12.0,
            put_bid=10.0, put_ask=11.0,
        ),
        high=SimpleNamespace(
            call_bid=3.0, call_ask=4.0,
            put_bid=2.0, put_ask=3.0,
        ),
    )

    long_trade = _paper_trade([
        {"instrument": "LOW_CALL", "side": "BUY", "price": 10.0},
        {"instrument": "LOW_PUT", "side": "BUY", "price": 9.0},
        {"instrument": "HIGH_CALL", "side": "SELL", "price": 5.0},
        {"instrument": "HIGH_PUT", "side": "SELL", "price": 4.0},
    ])
    assert _executable_paper_pnl(long_trade, row) == 4.0

    short_trade = _paper_trade([
        {"instrument": "LOW_CALL", "side": "SELL", "price": 10.0},
        {"instrument": "LOW_PUT", "side": "SELL", "price": 9.0},
        {"instrument": "HIGH_CALL", "side": "BUY", "price": 5.0},
        {"instrument": "HIGH_PUT", "side": "BUY", "price": 4.0},
    ])
    assert _executable_paper_pnl(short_trade, row) == 4.0


def test_expiry_close_is_strictly_at_session_boundary_for_nse(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=10_000_000, emergency_stop=False))
    db_session.commit()
    trade, _ = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="NIFTY",
        event_id="BOUND-NSE", direction="LONG", expiry="2026-10-12",
        earliest_expiry="2026-10-12", lot_size=50, lots=1, edge=4,
        capital_used=100000, metadata={"exchange": "NFO"},
    )
    assert svc.close_expired(db_session, now=datetime(2026, 10, 12, 15, 29, 59)) == []
    assert trade.status == "ONGOING"
    closed = svc.close_expired(db_session, now=datetime(2026, 10, 12, 15, 30, 0))
    assert len(closed) == 1
    assert closed[0].status == "COMPLETED"


def test_expiry_close_uses_mcx_session_boundary(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=10_000_000, emergency_stop=False))
    db_session.commit()
    trade, _ = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="GOLD",
        event_id="BOUND-MCX", direction="LONG", expiry="2026-10-12",
        earliest_expiry="2026-10-12", lot_size=1, lots=1, edge=4,
        capital_used=100000, metadata={"exchange": "MCX"},
    )
    assert svc.close_expired(db_session, now=datetime(2026, 10, 12, 23, 29, 59)) == []
    assert trade.status == "ONGOING"
    closed = svc.close_expired(db_session, now=datetime(2026, 10, 12, 23, 30, 0))
    assert len(closed) == 1
    assert closed[0].status == "COMPLETED"


def test_expiry_close_normalizes_aware_datetime_to_ist(db_session):
    from datetime import timezone, timedelta
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=10_000_000, emergency_stop=False))
    db_session.commit()
    trade, _ = svc.enter_or_mark(
        db_session, strategy_id="synthetic-future-cash-carry", symbol="NIFTY",
        event_id="TZ-1", direction="LONG", expiry="2026-10-12",
        earliest_expiry="2026-10-12", lot_size=50, lots=1, edge=4,
        capital_used=100000, metadata={"exchange": "NFO"},
    )
    # 10:00 UTC is 15:30 IST and must close exactly at the NSE boundary.
    closed = svc.close_expired(
        db_session,
        now=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
    )
    assert len(closed) == 1
    assert trade.status == "COMPLETED"


def test_expiry_close_does_not_close_before_earliest_expiry(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=10_000_000, emergency_stop=False))
    db_session.commit()
    trade, _ = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="NIFTY",
        event_id="EARLY-GUARD", direction="LONG", expiry="2026-10-30",
        earliest_expiry="2026-10-12", lot_size=50, lots=1, edge=4,
        capital_used=100000, metadata={"exchange": "NFO"},
    )
    assert svc.close_expired(db_session, now=datetime(2026, 10, 11, 23, 59, 59)) == []
    assert trade.status == "ONGOING"


def test_executable_pnl_returns_none_when_exit_quote_is_unavailable():
    from app.main import _executable_paper_pnl
    trade = _paper_trade([
        {"instrument": "CASH", "side": "BUY", "price": 100.0},
    ])
    row = {"cash_bid": None, "cash_ask": None}
    assert _executable_paper_pnl(trade, row) is None


def test_executable_pnl_cash_future_loss_uses_buy_bid_and_sell_ask():
    from app.main import _executable_paper_pnl
    trade = _paper_trade([
        {"instrument": "CASH", "side": "BUY", "price": 105.0},
        {"instrument": "FUTURE", "side": "SELL", "price": 100.0},
    ], lot_size=10, lots=2)
    row = {"cash_bid": 103.0, "cash_ask": 104.0, "future_bid": 102.0, "future_ask": 103.0}
    # BUY exits at bid: -2; SELL exits at ask: -3; total -5 * 20 = -100.
    assert _executable_paper_pnl(trade, row) == -100.0


def test_executable_pnl_calendar_mixed_legs_preserves_sell_sign():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace
    trade = _paper_trade([
        {"contract": "NEAR", "side": "SELL", "price": 110.0},
        {"contract": "FAR", "side": "BUY", "price": 100.0},
    ], lot_size=5, lots=2)
    row = SimpleNamespace(
        near_contract_month="NEAR", far_contract_month="FAR",
        near_bid=106.0, near_ask=107.0, far_bid=103.0, far_ask=104.0,
    )
    # SELL exits at ask: +3; BUY exits at bid: +3; total +6 * 10 = +60.
    assert _executable_paper_pnl(trade, row) == 60.0


def test_executable_pnl_synthetic_mixed_legs_can_realize_loss():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace
    trade = _paper_trade([
        {"instrument": "FUTURE", "side": "SELL", "price": 100.0},
        {"instrument": "CALL", "side": "BUY", "price": 10.0},
        {"instrument": "PUT", "side": "SELL", "price": 8.0},
    ], lot_size=10, lots=1)
    row = SimpleNamespace(
        future=SimpleNamespace(bid=101.0, ask=102.0),
        option=SimpleNamespace(call_bid=8.0, call_ask=9.0, put_bid=9.0, put_ask=10.0),
    )
    # SELL future: -2; BUY call: -2; SELL put: -2 => -6 * 10 = -60.
    assert _executable_paper_pnl(trade, row) == -60.0


def test_executable_pnl_box_mixed_legs_preserves_each_exit_side():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace
    trade = _paper_trade([
        {"instrument": "LOW_CALL", "side": "BUY", "price": 10.0},
        {"instrument": "LOW_PUT", "side": "SELL", "price": 9.0},
        {"instrument": "HIGH_CALL", "side": "SELL", "price": 5.0},
        {"instrument": "HIGH_PUT", "side": "BUY", "price": 4.0},
    ], lot_size=5, lots=2)
    row = SimpleNamespace(
        low=SimpleNamespace(call_bid=12.0, call_ask=13.0, put_bid=7.0, put_ask=8.0),
        high=SimpleNamespace(call_bid=6.0, call_ask=7.0, put_bid=3.0, put_ask=4.0),
    )
    # BUY call +2, SELL put +1, SELL call -2, BUY put -1 => zero.
    assert _executable_paper_pnl(trade, row) == 0.0



def test_expiry_close_does_not_report_trade_after_manual_close_wins(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    db_session.commit()

    trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="RACE-EXPIRY",
        event_id="RACE-EXPIRY-REPORT", direction="LONG",
        expiry="2026-10-12", earliest_expiry="2026-10-12",
        lot_size=10, lots=1, edge=10, capital_used=50000,
        metadata={"exchange": "NFO"}, user_id=1,
    )
    assert created is True
    svc.mark(db_session, trade, edge=12, pnl_override=200.0)

    # Simulate the manual-close transaction winning the close race.
    manual = svc.close(db_session, trade, "MANUAL")
    assert manual.status == "COMPLETED"
    assert manual.exit_reason == "MANUAL"
    assert manual.realized_pnl == 200.0

    # The expiry pass must not falsely report this already-completed manual
    # closure as an EXPIRY_CLOSE.
    closed = svc.close_expired(
        db_session, now=datetime(2026, 10, 12, 15, 30),
    )
    assert closed == []

    db_session.expire_all()
    current = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.id == trade.id,
    ).one()
    assert current.status == "COMPLETED"
    assert current.exit_reason == "MANUAL"
    assert current.realized_pnl == 200.0

def test_expiry_close_realizes_last_executable_pnl(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=10_000_000, emergency_stop=False))
    db_session.commit()
    trade, _ = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="ABC",
        event_id="REALIZE-EXPIRY", direction="LONG", expiry="2026-10-12",
        earliest_expiry="2026-10-12", lot_size=100, lots=2, edge=5,
        capital_used=100000, metadata={"exchange": "NFO"},
    )
    svc.mark(db_session, trade, edge=6, pnl_override=3750.25)
    db_session.commit()
    closed = svc.close_expired(db_session, now=datetime(2026, 10, 12, 15, 30))
    assert len(closed) == 1
    assert closed[0].realized_pnl == 3750.25
    assert closed[0].unrealized_pnl == 3750.25
    assert closed[0].status == "COMPLETED"


def test_global_paper_cap_is_shared_across_ongoing_trades(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=150000, emergency_stop=False))
    db_session.commit()
    first, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="CAP-1", direction="LONG", lot_size=10, lots=10,
        edge=5, capital_used=100000, user_id=1,
    )
    assert created is True
    second, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="BBB",
        event_id="CAP-2", direction="LONG", lot_size=10, lots=10,
        edge=5, capital_used=100000, user_id=1,
    )
    assert created is False
    assert second is None
    assert first.capital_used == 100000


def test_global_paper_cap_serializes_concurrent_entries(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    setup.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    setup.commit()
    setup.close()

    results = []
    errors = []
    start = threading.Barrier(2)

    def worker(event_id):
        db = Session()
        try:
            start.wait(timeout=5)
            trade, created = LivePaperTradeService().enter_or_mark(
                db, strategy_id="cash-future", symbol=event_id,
                event_id=event_id, direction="LONG", lot_size=10, lots=1,
                edge=5, capital_used=60000, user_id=1,
            )
            results.append((event_id, created, None if trade is None else trade.capital_used))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(event_id,)) for event_id in ("RACE-A", "RACE-B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2
    assert sum(1 for _, created, _ in results if created) == 1
    assert sum(float(capital or 0) for _, created, capital in results if created) == 60000

    verify = Session()
    try:
        ongoing = LivePaperTradeService().ongoing(verify, 1)
        assert len(ongoing) == 1
        assert ongoing[0].capital_used == 60000
    finally:
        verify.close()
        engine.dispose()


def test_same_event_concurrent_entries_create_one_trade_and_one_reservation(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'same-event-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    setup.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    setup.commit()
    setup.close()

    results = []
    errors = []
    start = threading.Barrier(2)

    def worker(edge):
        db = Session()
        try:
            start.wait(timeout=5)
            trade, created = LivePaperTradeService().enter_or_mark(
                db, strategy_id="cash-future", symbol="NIFTY",
                event_id="SAME-EVENT-RACE", direction="LONG",
                expiry="2026-10-30", earliest_expiry="2026-10-30",
                lot_size=10, lots=1, edge=edge, capital_used=60000, user_id=1,
            )
            results.append((
                created,
                None if trade is None else trade.id,
                None if trade is None else trade.capital_used,
                None if trade is None else trade.entry_edge,
                None if trade is None else trade.current_edge,
            ))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=(5.0,)),
        threading.Thread(target=worker, args=(7.0,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2
    assert sum(1 for created, *_ in results if created) == 1
    assert sum(1 for created, *_ in results if not created) == 1
    assert all(capital == 60000 for _, _, capital, _, _ in results)

    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.event_id == "SAME-EVENT-RACE",
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "ONGOING"
        assert rows[0].capital_used == 60000
        assert rows[0].lots == 1
        assert rows[0].entry_edge in {5.0, 7.0}
        assert rows[0].current_edge in {5.0, 7.0}
        assert rows[0].id == results[0][1] == results[1][1]
    finally:
        verify.close()
        engine.dispose()


def test_global_paper_cap_allows_only_remaining_lots(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=250000, emergency_stop=False))
    db_session.commit()
    first, _ = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="CAP-3", direction="LONG", lot_size=10, lots=10,
        edge=5, capital_used=100000, user_id=1,
    )
    second, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="BBB",
        event_id="CAP-4", direction="LONG", lot_size=10, lots=10,
        edge=5, capital_used=100000, user_id=1,
    )
    assert created is True
    assert second.lots == 10
    assert second.capital_used == 100000
    third, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="CCC",
        event_id="CAP-5", direction="LONG", lot_size=10, lots=10,
        edge=5, capital_used=100000, user_id=1,
    )
    assert created is True
    assert third.lots == 5
    assert third.capital_used == 50000

def test_completed_paper_event_cannot_reopen(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=500000, emergency_stop=False))
    db_session.commit()

    trade, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="NIFTY",
        event_id="REOPEN-GUARD",
        direction="LONG",
        expiry="2026-10-30",
        earliest_expiry="2026-10-30",
        lot_size=50,
        lots=1,
        edge=5,
        capital_used=100000,
        user_id=1,
    )
    assert created is True
    svc.mark(db_session, trade, edge=8, pnl_override=1500.0)
    svc.close(db_session, trade, "MANUAL")

    reopened, reopened_created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="NIFTY",
        event_id="REOPEN-GUARD",
        direction="LONG",
        expiry="2026-10-30",
        earliest_expiry="2026-10-30",
        lot_size=50,
        lots=1,
        edge=20,
        capital_used=100000,
        user_id=1,
    )

    assert reopened is None
    assert reopened_created is False
    assert svc.ongoing(db_session, 1) == []
    completed = svc.completed(db_session, 1)
    assert len(completed) == 1
    assert completed[0].id == trade.id
    assert completed[0].realized_pnl == 1500.0
    assert completed[0].current_edge == 8

def test_manual_close_and_duplicate_event_race_cannot_reopen(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'close-duplicate-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    setup.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=1000000, emergency_stop=False))
    setup.commit()
    trade, created = LivePaperTradeService().enter_or_mark(
        setup,
        strategy_id="cash-future",
        symbol="NIFTY",
        event_id="CLOSE-DUP-RACE",
        direction="LONG",
        expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=100000,
        user_id=1,
    )
    assert created is True
    trade_id = trade.id
    setup.close()
    
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def close_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            row = db.query(LivePaperTrade).filter(LivePaperTrade.id == trade_id).first()
            if row is not None and row.status == "ONGOING":
                result = LivePaperTradeService().close(db, row, "MANUAL")
                results.append(("close", result.status))
            else:
                results.append(("close", None))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def duplicate_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            result, created = LivePaperTradeService().enter_or_mark(
                db,
                strategy_id="cash-future",
                symbol="NIFTY",
                event_id="CLOSE-DUP-RACE",
                direction="LONG",
                expiry="2026-10-30",
                lot_size=10,
                lots=1,
                edge=15,
                capital_used=999999,
                user_id=1,
            )
            results.append(("duplicate", created, None if result is None else result.id))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=close_worker),
        threading.Thread(target=duplicate_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.event_id == "CLOSE-DUP-RACE",
        ).all()
        assert len(rows) == 1
        assert rows[0].id == trade_id
        assert rows[0].status == "COMPLETED"
        assert rows[0].capital_used == 100000
        assert LivePaperTradeService().ongoing(verify, 1) == []
        assert [x.id for x in LivePaperTradeService().completed(verify, 1)] == [trade_id]
    finally:
        verify.close()
        engine.dispose()


def test_manual_close_releases_reserved_capital_for_next_entry(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db_session.commit()

    first, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="RELEASE-MANUAL-1", direction="LONG", lot_size=10, lots=1,
        edge=5, capital_used=60000, user_id=1,
    )
    assert created is True
    assert first.capital_used == 60000

    blocked, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="BBB",
        event_id="RELEASE-MANUAL-BLOCK", direction="LONG", lot_size=10, lots=1,
        edge=5, capital_used=60000, user_id=1,
    )
    assert blocked is None
    assert created is False

    svc.close(db_session, first, "MANUAL")

    second, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="BBB",
        event_id="RELEASE-MANUAL-2", direction="LONG", lot_size=10, lots=1,
        edge=5, capital_used=60000, user_id=1,
    )
    assert created is True
    assert second.capital_used == 60000
    assert first.status == "COMPLETED"
    assert svc.ongoing(db_session, 1) == [second]


def test_alert_entry_resolves_expired_position_before_risk_gates(db_session):
    from app.models import AlertRule
    from app.notifications.common import AlertEvent, AlertService

    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0.0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=100000.0, max_simultaneous_positions=1, max_loss=1000.0,
    ))
    db_session.commit()

    svc = LivePaperTradeService()
    expired, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="EXPIRED",
        event_id="ALERT-EXPIRY-OLD", direction="LONG", expiry="2026-10-03",
        earliest_expiry="2026-10-03", lot_size=10, lots=1, edge=10,
        capital_used=60000, metadata={"exchange": "NFO"}, user_id=1,
    )
    assert created is True
    svc.mark(db_session, expired, edge=12)
    db_session.commit()

    # The background monitor is periodic; dispatch must resolve a due expiry
    # before simultaneous-position and capital gates evaluate the new alert.
    event = AlertEvent(
        strategy_id="cash-future", event_id="ALERT-EXPIRY-NEW", symbol="NEW",
        timestamp_ns=1, message="new", metadata={
            "gross_profit": 10,
            "paper_trade": {
                "direction": "LONG", "expiry": "2026-10-30",
                "lot_size": 10, "lots": 1, "edge": 5, "capital_used": 60000,
            },
        },
    )
    assert AlertService().dispatch(db_session, event) == 0

    db_session.expire_all()
    rows = svc.ongoing(db_session, 1)
    completed = svc.completed(db_session, 1)
    assert len(rows) == 1
    assert rows[0].event_id == "ALERT-EXPIRY-NEW"
    assert len(completed) == 1
    assert completed[0].event_id == "ALERT-EXPIRY-OLD"
    assert completed[0].exit_reason == "EXPIRY_CLOSE"


def test_alert_entry_keeps_expiry_realized_loss_in_max_loss_gate(db_session):
    from app.models import AlertRule
    from app.notifications.common import AlertEvent, AlertService

    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=200000, emergency_stop=False))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0.0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=0.0, max_simultaneous_positions=5, max_loss=100.0,
    ))
    db_session.commit()

    svc = LivePaperTradeService()
    expired, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="LOSS-EXPIRY",
        event_id="ALERT-LOSS-OLD", direction="LONG", expiry="2026-10-03",
        earliest_expiry="2026-10-03", lot_size=10, lots=1, edge=10,
        capital_used=50000, metadata={"exchange": "NFO"}, user_id=1,
    )
    assert created is True
    svc.mark(db_session, expired, edge=0.0)
    db_session.commit()

    event = AlertEvent(
        strategy_id="cash-future", event_id="ALERT-LOSS-NEW", symbol="NEW",
        timestamp_ns=2, message="blocked", metadata={
            "gross_profit": 10,
            "paper_trade": {
                "direction": "LONG", "expiry": "2026-10-30",
                "lot_size": 10, "lots": 1, "edge": 5, "capital_used": 50000,
            },
        },
    )
    assert AlertService().dispatch(db_session, event) == 0

    db_session.expire_all()
    assert svc.ongoing(db_session, 1) == []
    completed = svc.completed(db_session, 1)
    assert len(completed) == 1
    assert completed[0].realized_pnl == -100.0


def test_expired_completed_event_cannot_reopen_on_duplicate_alert(db_session):
    from app.models import AlertRule
    from app.notifications.common import AlertEvent, AlertService

    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0.0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=0.0, max_simultaneous_positions=5, max_loss=0.0,
    ))
    db_session.commit()

    svc = LivePaperTradeService()
    trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="EXPIRED",
        event_id="EXPIRY-DUP-1", direction="LONG", expiry="2026-10-03",
        earliest_expiry="2026-10-03", lot_size=10, lots=1, edge=10,
        capital_used=50000, metadata={"exchange": "NFO"}, user_id=1,
    )
    assert created is True
    svc.mark(db_session, trade, edge=8)
    db_session.commit()

    first = AlertEvent(
        strategy_id="cash-future", event_id="EXPIRY-DUP-1", symbol="EXPIRED",
        timestamp_ns=1, message="expired", metadata={
            "gross_profit": 1,
            "paper_trade": {
                "direction": "LONG", "expiry": "2026-10-03",
                "earliest_expiry": "2026-10-03", "lot_size": 10, "lots": 1,
                "edge": 8, "capital_used": 50000, "exchange": "NFO",
            },
        },
    )
    assert AlertService().dispatch(db_session, first) == 0

    db_session.expire_all()
    completed = svc.completed(db_session, 1)
    assert len(completed) == 1
    assert completed[0].id == trade.id
    assert completed[0].exit_reason == "EXPIRY_CLOSE"
    original_closed_at = completed[0].closed_at
    original_capital = completed[0].capital_used

    duplicate = AlertEvent(
        strategy_id="cash-future", event_id="EXPIRY-DUP-1", symbol="EXPIRED",
        timestamp_ns=2, message="duplicate-after-expiry", metadata={
            "gross_profit": 999,
            "paper_trade": {
                "direction": "LONG", "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30", "lot_size": 10, "lots": 99,
                "edge": 50, "capital_used": 999999, "exchange": "NFO",
            },
        },
    )
    assert AlertService().dispatch(db_session, duplicate) == 0

    db_session.expire_all()
    rows = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "EXPIRY-DUP-1",
    ).all()
    assert len(rows) == 1
    assert rows[0].id == trade.id
    assert rows[0].status == "COMPLETED"
    assert rows[0].capital_used == original_capital
    assert rows[0].closed_at == original_closed_at
    assert rows[0].current_edge == 8.0


def test_expiry_close_realizes_last_valid_mark_when_new_quote_is_stale(db_session):
    svc = LivePaperTradeService()
    trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="STALE-EXPIRY",
        event_id="STALE-EXPIRY-1", direction="LONG", expiry="2026-10-03",
        earliest_expiry="2026-10-03", lot_size=10, lots=1, edge=10,
        capital_used=50000, metadata={"exchange": "NFO"}, user_id=1,
    )
    assert created is True

    # Last valid executable mark is +50. No later fresh quote is available;
    # expiry close must realize that preserved mark, not invent a new edge.
    svc.mark(db_session, trade, edge=15, pnl_override=50.0)
    db_session.commit()
    closed = svc.close_expired(db_session, now=datetime(2026, 10, 3, 15, 30))

    assert [x.id for x in closed] == [trade.id]
    assert closed[0].status == "COMPLETED"
    assert closed[0].realized_pnl == 50.0
    assert closed[0].unrealized_pnl == 50.0


def test_expiry_close_releases_reserved_capital_for_next_entry(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db_session.commit()

    first, created = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="AAA",
        event_id="RELEASE-EXPIRY-1", direction="LONG",
        expiry="2026-10-12", earliest_expiry="2026-10-12",
        lot_size=10, lots=1, edge=5, capital_used=60000,
        metadata={"exchange": "NFO"}, user_id=1,
    )
    assert created is True

    blocked, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="BBB",
        event_id="RELEASE-EXPIRY-BLOCK", direction="LONG", lot_size=10, lots=1,
        edge=5, capital_used=60000, user_id=1,
    )
    assert blocked is None
    assert created is False

    closed = svc.close_expired(db_session, now=datetime(2026, 10, 12, 15, 30))
    assert len(closed) == 1
    assert closed[0].status == "COMPLETED"

    second, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="BBB",
        event_id="RELEASE-EXPIRY-2", direction="LONG", lot_size=10, lots=1,
        edge=5, capital_used=60000, user_id=1,
    )
    assert created is True
    assert second.capital_used == 60000
    assert svc.ongoing(db_session, 1) == [second]


def test_partial_manual_close_releases_only_allocated_capital_for_next_entry(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=125000, emergency_stop=False))
    db_session.commit()

    first, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="AAA",
        event_id="PARTIAL-RELEASE-1", direction="LONG", lot_size=10, lots=3,
        edge=5, capital_used=90000, user_id=1,
    )
    assert created is True

    partial, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="BBB",
        event_id="PARTIAL-RELEASE-2", direction="LONG", lot_size=10, lots=2,
        edge=5, capital_used=60000, user_id=1,
    )
    assert created is True
    assert partial.lots == 1
    assert partial.capital_used == 30000

    # Only 5k remains before close. Closing the partial position must release
    # exactly its 30k reservation, not the original requested 60k.
    svc.close(db_session, partial, "MANUAL")

    next_trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="CCC",
        event_id="PARTIAL-RELEASE-3", direction="LONG", lot_size=10, lots=1,
        edge=5, capital_used=30000, user_id=1,
    )
    assert created is True
    assert next_trade.lots == 1
    assert next_trade.capital_used == 30000
    assert first.status == "ONGOING"
    assert partial.status == "COMPLETED"


def test_partial_expiry_close_releases_allocated_capital_for_next_entry(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=125000, emergency_stop=False))
    db_session.commit()

    first, created = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="AAA",
        event_id="PARTIAL-EXPIRY-1", direction="LONG",
        expiry="2026-10-12", earliest_expiry="2026-10-12",
        lot_size=10, lots=3, edge=5, capital_used=90000,
        metadata={"exchange": "NFO"}, user_id=1,
    )
    assert created is True

    partial, created = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="BBB",
        event_id="PARTIAL-EXPIRY-2", direction="LONG",
        expiry="2026-10-12", earliest_expiry="2026-10-12",
        lot_size=10, lots=2, edge=5, capital_used=60000,
        metadata={"exchange": "NFO"}, user_id=1,
    )
    assert created is True
    assert partial.lots == 1
    assert partial.capital_used == 30000

    closed = svc.close_expired(db_session, now=datetime(2026, 10, 12, 15, 30))
    assert [row.id for row in closed] == [partial.id]

    next_trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="CCC",
        event_id="PARTIAL-EXPIRY-3", direction="LONG", lot_size=10, lots=1,
        edge=5, capital_used=30000, user_id=1,
    )
    assert created is True
    assert next_trade.capital_used == 30000
    assert first.status == "ONGOING"
    assert partial.status == "COMPLETED"


def test_close_and_new_entry_race_never_oversubscribes_capital(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'close-entry-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    setup.commit()
    original, created = LivePaperTradeService().enter_or_mark(
        setup, strategy_id="cash-future", symbol="AAA",
        event_id="CLOSE-ENTRY-RACE-ORIGINAL", direction="LONG",
        lot_size=10, lots=1, edge=5, capital_used=60000, user_id=1,
    )
    assert created is True
    original_id = original.id
    setup.close()

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def close_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            row = db.query(LivePaperTrade).filter(LivePaperTrade.id == original_id).first()
            if row is not None and row.status == "ONGOING":
                closed = LivePaperTradeService().close(db, row, "MANUAL")
                results.append(("close", closed.status))
            else:
                results.append(("close", None))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def entry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade, created = LivePaperTradeService().enter_or_mark(
                db, strategy_id="cash-future", symbol="BBB",
                event_id="CLOSE-ENTRY-RACE-NEW", direction="LONG",
                lot_size=10, lots=1, edge=5, capital_used=60000, user_id=1,
            )
            results.append(("entry", created, None if trade is None else trade.capital_used))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=close_worker), threading.Thread(target=entry_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2

    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
        ).all()
        ongoing = [row for row in rows if row.status == "ONGOING"]
        completed = [row for row in rows if row.status == "COMPLETED"]

        assert len(completed) == 1
        assert completed[0].id == original_id
        assert len(ongoing) <= 1
        assert sum(float(row.capital_used or 0.0) for row in ongoing) <= 100000

        entry_results = [row for row in results if row[0] == "entry"]
        assert len(entry_results) == 1
        if entry_results[0][1]:
            assert entry_results[0][2] == 60000
    finally:
        verify.close()
        engine.dispose()



def test_close_and_multiple_new_entries_race_never_oversubscribes_capital(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'close-multi-entry-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    setup.commit()
    original, created = LivePaperTradeService().enter_or_mark(
        setup,
        strategy_id="cash-future",
        symbol="ORIGINAL",
        event_id="CLOSE-MULTI-RACE-ORIGINAL",
        direction="LONG",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=60000,
        user_id=1,
    )
    assert created is True
    original_id = original.id
    setup.close()

    barrier = threading.Barrier(4)
    results = []
    errors = []

    def close_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            row = db.query(LivePaperTrade).filter(
                LivePaperTrade.id == original_id,
                LivePaperTrade.status == "ONGOING",
            ).first()
            if row is not None:
                closed = LivePaperTradeService().close(db, row, "MANUAL")
                results.append(("close", closed.status, closed.exit_reason))
            else:
                results.append(("close", None, None))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def entry_worker(event_id):
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade, created = LivePaperTradeService().enter_or_mark(
                db,
                strategy_id="cash-future",
                symbol=event_id,
                event_id=event_id,
                direction="LONG",
                lot_size=10,
                lots=1,
                edge=5,
                capital_used=60000,
                user_id=1,
            )
            results.append((
                "entry",
                event_id,
                created,
                None if trade is None else trade.capital_used,
            ))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=close_worker),
        threading.Thread(target=entry_worker, args=("CLOSE-MULTI-RACE-A",)),
        threading.Thread(target=entry_worker, args=("CLOSE-MULTI-RACE-B",)),
        threading.Thread(target=entry_worker, args=("CLOSE-MULTI-RACE-C",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 4

    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
        ).all()
        ongoing = [row for row in rows if row.status == "ONGOING"]
        completed = [row for row in rows if row.status == "COMPLETED"]

        assert len(completed) == 1
        assert completed[0].id == original_id
        assert sum(float(row.capital_used or 0.0) for row in ongoing) <= 100000

        entry_results = [row for row in results if row[0] == "entry"]
        assert len(entry_results) == 3
        successful = [row for row in entry_results if row[2]]
        assert len(successful) <= 1
        for row in successful:
            assert row[3] == 60000
    finally:
        verify.close()
        engine.dispose()


def test_partial_allocation_and_expiry_close_race_preserve_capital(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'partial-expiry-entry-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    setup.commit()

    original, created = LivePaperTradeService().enter_or_mark(
        setup,
        strategy_id="calendar-spread",
        symbol="ORIGINAL",
        event_id="PARTIAL-EXPIRY-RACE-ORIGINAL",
        direction="LONG",
        expiry="2026-10-12",
        earliest_expiry="2026-10-12",
        lot_size=10,
        lots=2,
        edge=5,
        capital_used=60000,
        metadata={"exchange": "NFO"},
        user_id=1,
    )
    assert created is True
    assert original.lots == 2
    assert original.capital_used == 60000
    original_id = original.id
    setup.close()

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def expiry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            closed = LivePaperTradeService().close_expired(
                db, now=datetime(2026, 10, 12, 15, 30),
            )
            results.append(("expiry", len(closed)))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def entry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade, created = LivePaperTradeService().enter_or_mark(
                db,
                strategy_id="cash-future",
                symbol="NEW",
                event_id="PARTIAL-EXPIRY-RACE-NEW",
                direction="LONG",
                lot_size=10,
                lots=2,
                edge=5,
                capital_used=60000,






def test_duplicate_mark_manual_close_expiry_close_three_way_preserves_terminal_pnl(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'duplicate-manual-expiry-mark-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    setup.commit()
    original, created = LivePaperTradeService().enter_or_mark(
        setup,
        strategy_id="cash-future",
        symbol="RACE",
        event_id="THREE-WAY-MARK-CLOSE",
        direction="LONG",
        expiry="2026-10-12",
        earliest_expiry="2026-10-12",
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=30000,
        metadata={"exchange": "NFO"},
        user_id=1,
    )
    assert created is True
    original_id = original.id
    setup.close()

    barrier = threading.Barrier(3)
    results, errors = [], []

    def duplicate_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade, was_created = LivePaperTradeService().enter_or_mark(
                db,
                strategy_id="cash-future",
                symbol="RACE",
                event_id="THREE-WAY-MARK-CLOSE",
                direction="LONG",
                expiry="2026-10-12",
                earliest_expiry="2026-10-12",
                lot_size=99,
                lots=9,
                edge=20,
                capital_used=900000,
                metadata={"exchange": "NFO"},
                user_id=1,
            )
            results.append(("mark", was_created, None if trade is None else trade.id))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def manual_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            row = db.query(LivePaperTrade).filter(
                LivePaperTrade.id == original_id,
                LivePaperTrade.status == "ONGOING",
            ).first()
            if row is None:
                results.append(("manual", None))
            else:
                closed = LivePaperTradeService().close(db, row, "MANUAL")
                results.append(("manual", closed.exit_reason))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def expiry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            closed = LivePaperTradeService().close_expired(
                db, now=datetime(2026, 10, 12, 15, 30),
            )
            results.append(("expiry", len(closed)))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=duplicate_worker),
        threading.Thread(target=manual_worker),
        threading.Thread(target=expiry_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 3

    verify = Session()
    try:
        row = verify.query(LivePaperTrade).filter(
            LivePaperTrade.id == original_id,
        ).one()
        assert row.status == "COMPLETED"
        assert row.exit_reason in {"MANUAL", "EXPIRY_CLOSE"}
        assert row.lots == 1
        assert row.lot_size == 10
        assert row.capital_used == 30000
        assert row.entry_edge == 10
        assert row.realized_pnl == row.unrealized_pnl
        assert row.pnl_pct == round(row.realized_pnl / row.capital_used * 100.0, 8)
        assert row.closed_at is not None
        assert row.last_mark_at is not None
        assert row.last_mark_at <= row.closed_at
        assert verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.event_id == "THREE-WAY-MARK-CLOSE",
        ).count() == 1
    finally:
        verify.close()
        engine.dispose()

def test_duplicate_event_cannot_mark_after_concurrent_expiry_close(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'duplicate-expiry-mark-race.db'}", connect_args={"check_same_thread": False, "timeout": 5})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    setup.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    setup.commit()
    original, created = LivePaperTradeService().enter_or_mark(setup, strategy_id="cash-future", symbol="RACE", event_id="DUP-EXPIRY-RACE", direction="LONG", expiry="2026-10-12", earliest_expiry="2026-10-12", lot_size=10, lots=1, edge=10, capital_used=30000, metadata={"exchange": "NFO"}, user_id=1)
    assert created is True
    original_id = original.id
    setup.close()
    barrier = threading.Barrier(2)
    results, errors = [], []
    def expiry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            closed = LivePaperTradeService().close_expired(db, now=datetime(2026, 10, 12, 15, 30))
            results.append(("expiry", len(closed)))
        except Exception as exc: errors.append(exc)
        finally: db.close()
    def duplicate_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade, was_created = LivePaperTradeService().enter_or_mark(db, strategy_id="cash-future", symbol="RACE", event_id="DUP-EXPIRY-RACE", direction="LONG", expiry="2026-10-12", earliest_expiry="2026-10-12", lot_size=99, lots=9, edge=20, capital_used=900000, metadata={"exchange": "NFO"}, user_id=1)
            results.append(("duplicate", was_created, None if trade is None else trade.id, None if trade is None else trade.status))
        except Exception as exc: errors.append(exc)
        finally: db.close()
    threads = [threading.Thread(target=expiry_worker), threading.Thread(target=duplicate_worker)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=10)
    assert errors == []
    assert len(results) == 2
    verify = Session()
    try:
        row = verify.query(LivePaperTrade).filter(LivePaperTrade.id == original_id).one()
        assert row.status == "COMPLETED"
        assert row.exit_reason == "EXPIRY_CLOSE"
        assert row.lots == 1
        assert row.lot_size == 10
        assert row.capital_used == 30000
        assert row.entry_edge == 10
        assert row.current_edge in {10, 20}
        assert verify.query(LivePaperTrade).filter(LivePaperTrade.user_id == 1, LivePaperTrade.event_id == "DUP-EXPIRY-RACE").count() == 1\n        assert row.realized_pnl == row.unrealized_pnl
    finally:
        verify.close()
        engine.dispose()

def test_manual_expiry_close_and_new_entry_three_way_race_is_consistent(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import AlertRule

    engine = create_engine(f"sqlite:///{tmp_path / 'manual-expiry-entry-three-way.db'}", connect_args={"check_same_thread": False, "timeout": 5})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    setup.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    setup.add(AlertRule(user_id=1, strategy_id="cash-future", min_gross_profit=0, mobile_number="", whatsapp_enabled=False, enabled=True, max_daily_capital=0, max_simultaneous_positions=1, max_loss=0))
    setup.commit()
    original, created = LivePaperTradeService().enter_or_mark(setup, strategy_id="cash-future", symbol="RACE-ORIGINAL", event_id="THREE-WAY-ORIGINAL", direction="LONG", expiry="2026-10-12", earliest_expiry="2026-10-12", lot_size=10, lots=1, edge=5, capital_used=30000, metadata={"exchange": "NFO"}, user_id=1)
    assert created is True
    original_id = original.id
    setup.close()

    barrier = threading.Barrier(3)
    results, errors = [], []
    def manual_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            row = db.query(LivePaperTrade).filter(LivePaperTrade.id == original_id, LivePaperTrade.status == "ONGOING").first()
            results.append(("manual", None if row is None else LivePaperTradeService().close(db, row, "MANUAL").exit_reason))
        except Exception as exc: errors.append(exc)
        finally: db.close()
    def expiry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            closed = LivePaperTradeService().close_expired(db, now=datetime(2026, 10, 12, 15, 30))
            results.append(("expiry", len(closed)))
        except Exception as exc: errors.append(exc)
        finally: db.close()
    def entry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            event = AlertEvent(strategy_id="cash-future", event_id="THREE-WAY-NEW", symbol="NEW", timestamp_ns=1, message="three-way-race", metadata={"gross_profit": 1000, "paper_trade": {"direction": "LONG", "expiry": "2026-10-30", "lot_size": 10, "lots": 1, "edge": 5, "capital_used": 30000}})
            results.append(("entry", AlertService().dispatch(db, event)))
        except Exception as exc: errors.append(exc)
        finally: db.close()
    threads = [threading.Thread(target=manual_worker), threading.Thread(target=expiry_worker), threading.Thread(target=entry_worker)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=10)
    assert errors == []
    assert len(results) == 3

    verify = Session()
    try:
        original_row = verify.query(LivePaperTrade).filter(LivePaperTrade.id == original_id).one()
        new_rows = verify.query(LivePaperTrade).filter(LivePaperTrade.user_id == 1, LivePaperTrade.event_id == "THREE-WAY-NEW").all()
        ongoing = verify.query(LivePaperTrade).filter(LivePaperTrade.user_id == 1, LivePaperTrade.status == "ONGOING").all()
        assert original_row.status == "COMPLETED"
        assert original_row.exit_reason in {"MANUAL", "EXPIRY_CLOSE"}
        assert original_row.closed_at is not None
        assert len(new_rows) <= 1
        assert len(ongoing) <= 1
        assert sum(float(row.capital_used or 0.0) for row in ongoing) <= 100000
        if new_rows:
            assert new_rows[0].status == "ONGOING"
            assert new_rows[0].capital_used == 30000
            assert new_rows[0].lots == 1
        assert len([row for row in [original_row] if row.status == "COMPLETED"]) == 1
    finally:
        verify.close()
        engine.dispose()

def test_expiry_close_and_new_entry_race_respects_position_slot(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import AlertRule

    engine = create_engine(f"sqlite:///{tmp_path / 'expiry-position-slot-race.db'}", connect_args={"check_same_thread": False, "timeout": 5})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    setup.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    setup.add(AlertRule(user_id=1, strategy_id="cash-future", min_gross_profit=0, mobile_number="", whatsapp_enabled=False, enabled=True, max_daily_capital=0, max_simultaneous_positions=1, max_loss=0))
    setup.commit()
    original, created = LivePaperTradeService().enter_or_mark(setup, strategy_id="cash-future", symbol="EXPIRING", event_id="EXPIRY-SLOT-RACE-ORIGINAL", direction="LONG", expiry="2026-10-12", earliest_expiry="2026-10-12", lot_size=10, lots=1, edge=5, capital_used=30000, metadata={"exchange": "NFO"}, user_id=1)
    assert created is True
    original_id = original.id
    setup.close()
    barrier = threading.Barrier(2)
    results, errors = [], []
    def expiry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            closed = LivePaperTradeService().close_expired(db, now=datetime(2026, 10, 12, 15, 30))
            results.append(("expiry", len(closed)))
        except Exception as exc: errors.append(exc)
        finally: db.close()
    def entry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            event = AlertEvent(strategy_id="cash-future", event_id="EXPIRY-SLOT-RACE-NEW", symbol="NEW", timestamp_ns=1, message="expiry-slot-race", metadata={"gross_profit": 1000, "paper_trade": {"direction": "LONG", "expiry": "2026-10-30", "lot_size": 10, "lots": 1, "edge": 5, "capital_used": 30000}})
            results.append(("entry", AlertService().dispatch(db, event)))
        except Exception as exc: errors.append(exc)
        finally: db.close()
    threads = [threading.Thread(target=expiry_worker), threading.Thread(target=entry_worker)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=10)
    assert errors == []
    assert len(results) == 2
    verify = Session()
    try:
        original_row = verify.query(LivePaperTrade).filter(LivePaperTrade.id == original_id).one()
        new_rows = verify.query(LivePaperTrade).filter(LivePaperTrade.user_id == 1, LivePaperTrade.event_id == "EXPIRY-SLOT-RACE-NEW").all()
        ongoing = verify.query(LivePaperTrade).filter(LivePaperTrade.user_id == 1, LivePaperTrade.status == "ONGOING").all()
        assert original_row.status == "COMPLETED"
        assert original_row.exit_reason == "EXPIRY_CLOSE"
        assert len(new_rows) <= 1
        assert len(ongoing) <= 1
        assert sum(float(row.capital_used or 0.0) for row in ongoing) <= 100000
        if new_rows:
            assert new_rows[0].status == "ONGOING"
            assert new_rows[0].capital_used == 30000
            assert new_rows[0].lots == 1
    finally:
        verify.close()
        engine.dispose()

def test_partial_duplicate_event_expiry_race_allocates_once_and_never_oversubscribes(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'partial-duplicate-expiry-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    setup.commit()
    original, created = LivePaperTradeService().enter_or_mark(
        setup,
        strategy_id="cash-future",
        symbol="ORIGINAL",
        event_id="COMBINED-RACE-ORIGINAL",
        direction="LONG",
        expiry="2026-10-12",
        earliest_expiry="2026-10-12",
        lot_size=10,
        lots=2,
        edge=5,
        capital_used=60000,
        metadata={"exchange": "NFO"},
        user_id=1,
    )
    assert created is True
    assert original.lots == 2
    assert original.capital_used == 60000
    original_id = original.id
    setup.close()

    barrier = threading.Barrier(4)
    results = []
    errors = []

    def expiry_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            closed = LivePaperTradeService().close_expired(
                db, now=datetime(2026, 10, 12, 15, 30),
            )
            results.append(("expiry", len(closed)))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def duplicate_worker(edge):
        db = Session()
        try:
            barrier.wait(timeout=5)
            trade, created = LivePaperTradeService().enter_or_mark(
                db,
                strategy_id="cash-future",
                symbol="DUPLICATE",
                event_id="COMBINED-RACE-NEW",
                direction="LONG",
                expiry="2026-10-30",
                earliest_expiry="2026-10-30",
                lot_size=10,
                lots=2,
                edge=edge,
                capital_used=60000,
                user_id=1,
            )
            results.append((
                "entry", created,
                None if trade is None else trade.id,
                None if trade is None else trade.lots,
                None if trade is None else trade.capital_used,
            ))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=expiry_worker),
        threading.Thread(target=duplicate_worker, args=(5.0,)),
        threading.Thread(target=duplicate_worker, args=(7.0,)),
        threading.Thread(target=duplicate_worker, args=(9.0,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 4

    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
        ).all()
        original_row = verify.query(LivePaperTrade).filter(
            LivePaperTrade.id == original_id,
        ).one()
        new_rows = [row for row in rows if row.event_id == "COMBINED-RACE-NEW"]
        ongoing = [row for row in rows if row.status == "ONGOING"]

        assert original_row.status == "COMPLETED"
        assert original_row.exit_reason == "EXPIRY_CLOSE"
        assert len(new_rows) == 1
        assert new_rows[0].status == "ONGOING"
        assert new_rows[0].lots in {1, 2}
        assert new_rows[0].capital_used == new_rows[0].lots * 30000
        assert sum(float(row.capital_used or 0.0) for row in ongoing) <= 100000

        entry_results = [row for row in results if row[0] == "entry"]
        assert len(entry_results) == 3
        assert sum(1 for row in entry_results if row[1]) == 1
        successful_ids = {row[2] for row in entry_results if row[1]}
        assert successful_ids == {new_rows[0].id}
    finally:
        verify.close()
        engine.dispose()


def test_completed_partial_duplicate_event_never_reopens_or_reallocates(db_session):
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    db_session.commit()

    seed, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="SEED",
        event_id="COMPLETED-DUP-SEED",
        direction="LONG",
        lot_size=10,
        lots=2,
        edge=5,
        capital_used=60000,
        user_id=1,
    )
    assert created is True

    trade, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="DUP",
        event_id="COMPLETED-DUP-EVENT",
        direction="LONG",
        lot_size=10,
        lots=2,
        edge=10,
        capital_used=60000,
        user_id=1,
    )
    assert created is True
    assert trade.lots == 1
    assert trade.capital_used == 30000
    trade_id = trade.id

    svc.close(db_session, trade, "EXPIRY_CLOSE")

    duplicate, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="DUP",
        event_id="COMPLETED-DUP-EVENT",
        direction="LONG",
        lot_size=10,
        lots=2,
        edge=99,
        capital_used=60000,
        user_id=1,
    )
    assert duplicate is None
    assert created is False

    db_session.expire_all()
    rows = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "COMPLETED-DUP-EVENT",
    ).all()
    assert len(rows) == 1
    assert rows[0].id == trade_id
    assert rows[0].status == "COMPLETED"
    assert rows[0].capital_used == 30000
    assert rows[0].current_edge == 10.0
    assert svc.ongoing(db_session, 1) == [seed]


def test_executable_pnl_calendar_long_direction_uses_persisted_legs():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace
    trade = LivePaperTrade(
        lot_size=1, lots=1,
        legs_json='[{"contract":"NEAR","side":"BUY","price":101.0},{"contract":"FAR","side":"SELL","price":106.0}]',
    )
    row = SimpleNamespace(
        near_contract_month="NEAR", far_contract_month="FAR",
        near_bid=99.0, near_ask=100.0, far_bid=102.0, far_ask=103.0,
    )
    # LONG_NEAR_SHORT_FAR is normalized to ledger LONG, but P&L is derived
    # from the actual BUY/SELL legs: (99-101) + (106-103) = +1.
    assert _executable_paper_pnl(trade, row) == 1.0


def test_executable_pnl_calendar_short_direction_uses_persisted_legs():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace
    trade = LivePaperTrade(
        lot_size=1, lots=1,
        legs_json='[{"contract":"NEAR","side":"SELL","price":100.0},{"contract":"FAR","side":"BUY","price":107.0}]',
    )
    row = SimpleNamespace(
        near_contract_month="NEAR", far_contract_month="FAR",
        near_bid=97.0, near_ask=98.0, far_bid=109.0, far_ask=110.0,
    )
    # SHORT_NEAR_LONG_FAR: (100-98) + (109-107) = +4.
    assert _executable_paper_pnl(trade, row) == 4.0


def test_executable_pnl_synthetic_both_directions_follow_leg_sides():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace

    option = SimpleNamespace(call_bid=9.0, call_ask=11.0, put_bid=4.0, put_ask=6.0)
    future = SimpleNamespace(bid=108.0, ask=110.0)
    row = SimpleNamespace(option=option, future=future)

    long_trade = LivePaperTrade(
        lot_size=1, lots=1,
        legs_json='[{"instrument":"FUTURE","side":"BUY","price":105.0},{"instrument":"CALL","side":"BUY","price":10.0},{"instrument":"PUT","side":"SELL","price":5.0}]',
    )
    short_trade = LivePaperTrade(
        lot_size=1, lots=1,
        legs_json='[{"instrument":"FUTURE","side":"SELL","price":105.0},{"instrument":"CALL","side":"SELL","price":10.0},{"instrument":"PUT","side":"BUY","price":5.0}]',
    )

    # LONG: (108-105) + (9-10) + (5-6) = +1.
    # SHORT: (105-110) + (10-11) + (4-5) = -7.
    assert _executable_paper_pnl(long_trade, row) == 1.0
    assert _executable_paper_pnl(short_trade, row) == -7.0


def test_executable_pnl_box_both_directions_follow_leg_sides():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace

    low = SimpleNamespace(call_bid=12.0, call_ask=13.0, put_bid=10.0, put_ask=11.0)
    high = SimpleNamespace(call_bid=7.0, call_ask=8.0, put_bid=6.0, put_ask=7.0)
    row = SimpleNamespace(low=low, high=high)

    long_trade = LivePaperTrade(
        lot_size=1, lots=1,
        legs_json='[{"instrument":"LOW_CALL","side":"BUY","price":10.0},{"instrument":"LOW_PUT","side":"BUY","price":9.0},{"instrument":"HIGH_CALL","side":"SELL","price":5.0},{"instrument":"HIGH_PUT","side":"SELL","price":4.0}]',
    )
    short_trade = LivePaperTrade(
        lot_size=1, lots=1,
        legs_json='[{"instrument":"LOW_CALL","side":"SELL","price":10.0},{"instrument":"LOW_PUT","side":"SELL","price":9.0},{"instrument":"HIGH_CALL","side":"BUY","price":5.0},{"instrument":"HIGH_PUT","side":"BUY","price":4.0}]',
    )

    # LONG: +2 +1 -3 -3 = -3.
    # SHORT: (10-13) + (9-11) + (7-5) + (6-4) = -1.
    assert _executable_paper_pnl(long_trade, row) == -3.0
    assert _executable_paper_pnl(short_trade, row) == -1.0


def test_strategy_capital_definitions_scale_with_actual_allocated_lots_and_pnl_pct(db_session):
    """Calendar/Synthetic/Box all mark against persisted allocated lots and capital."""
    svc = LivePaperTradeService()
    db_session.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db_session.commit()

    cases = [
        ("calendar-spread", "CAL-CAP-PNL", 50, 2, 60000, 1, 30000, 120.0),
        ("synthetic-future-cash-carry", "SYN-CAP-PNL", 50, 2, 60000, 1, 30000, 80.0),
        ("box-spread", "BOX-CAP-PNL", 50, 2, 40000, 1, 20000, 60.0),
    ]
    for strategy, event_id, lot_size, requested_lots, requested_capital, expected_lots, expected_capital, pnl in cases:
        trade, created = svc.enter_or_mark(
            db_session,
            strategy_id=strategy,
            symbol=event_id,
            event_id=event_id,
            direction="LONG",
            expiry="2026-10-30",
            earliest_expiry="2026-10-30",
            lot_size=lot_size,
            lots=requested_lots,
            edge=5,
            capital_used=requested_capital,
            metadata={"exchange": "NFO"},
            user_id=1,
        )
        assert created is True
        assert trade.lots == expected_lots
        assert trade.capital_used == expected_capital

        # P&L is deliberately supplied here to isolate ledger accounting from
        # quote mapping: denominator must remain the actual allocated capital.
        svc.mark(db_session, trade, edge=6, pnl_override=pnl)
        assert trade.lots == expected_lots
        assert trade.capital_used == expected_capital
        assert trade.unrealized_pnl == pnl
        assert trade.pnl_pct == round(pnl / expected_capital * 100.0, 8)

        svc.close(db_session, trade, "MANUAL")
        assert trade.realized_pnl == pnl
        assert trade.unrealized_pnl == pnl
        assert trade.pnl_pct == round(pnl / expected_capital * 100.0, 8)


def test_strategy_executable_pnl_and_persisted_capital_use_allocated_lot_count():
    """Executable multi-leg P&L is scaled by the persisted allocated lots, not request size."""
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace

    calendar = LivePaperTrade(
        lot_size=50, lots=1,
        legs_json='[{"contract":"NEAR","side":"BUY","price":100.0},{"contract":"FAR","side":"SELL","price":110.0}]',
    )
    cal_row = SimpleNamespace(
        near_contract_month="NEAR", far_contract_month="FAR",
        near_bid=103.0, near_ask=104.0, far_bid=108.0, far_ask=109.0,
    )
    # +3 on the BUY leg and +1 on the SELL leg = +4 points x one allocated lot.
    assert _executable_paper_pnl(calendar, cal_row) == 200.0

    synthetic = LivePaperTrade(
        lot_size=50, lots=1,
        legs_json='[{"instrument":"FUTURE","side":"BUY","price":100.0},{"instrument":"CALL","side":"BUY","price":10.0},{"instrument":"PUT","side":"SELL","price":8.0}]',
    )
    syn_row = SimpleNamespace(
        future=SimpleNamespace(bid=102.0, ask=103.0),
        option=SimpleNamespace(call_bid=12.0, call_ask=13.0, put_bid=7.0, put_ask=9.0),
    )
    # +2 -1 -1 = 0 points x one allocated lot.
    assert _executable_paper_pnl(synthetic, syn_row) == 0.0

    box = LivePaperTrade(
        lot_size=50, lots=1,
        legs_json='[{"instrument":"LOW_CALL","side":"BUY","price":10.0},{"instrument":"LOW_PUT","side":"BUY","price":9.0},{"instrument":"HIGH_CALL","side":"SELL","price":5.0},{"instrument":"HIGH_PUT","side":"SELL","price":4.0}]',
    )
    box_row = SimpleNamespace(
        low=SimpleNamespace(call_bid=11.0, call_ask=12.0, put_bid=10.0, put_ask=11.0),
        high=SimpleNamespace(call_bid=3.0, call_ask=4.0, put_bid=2.0, put_ask=3.0),
    )
    # +1 +1 +2 +2 = +6 points x one allocated lot.
    assert _executable_paper_pnl(box, box_row) == 300.0
