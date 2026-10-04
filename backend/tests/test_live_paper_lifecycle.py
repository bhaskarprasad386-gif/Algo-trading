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
