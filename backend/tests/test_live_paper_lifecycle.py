from datetime import datetime, time
from app.auto.live_paper import LivePaperTradeService
from app.models.live_paper_trade import LivePaperTrade

def test_live_paper_trade_lifecycle_and_ranking(db_session):
    svc = LivePaperTradeService()
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
    trade,_ = svc.enter_or_mark(db_session, strategy_id="calendar-spread", symbol="NIFTY",
        event_id="CAL-1", direction="LONG", expiry="2026-10-30", earliest_expiry="2026-10-10",
        lot_size=50, lots=1, edge=4, capital_used=100000)
    closed = svc.close_expired(db_session, now=datetime(2026,10,10,15,30))
    assert len(closed) == 1
    assert closed[0].status == "COMPLETED"
    assert closed[0].exit_reason == "EXPIRY_CLOSE"
