from datetime import date, datetime, timedelta

from app.core.database import Base, SessionLocal, engine
from app.models.cash_future_history import CashFutureHistory
from app.scanner.synchronized_replay import iter_persisted_symbol_replay


def test_persisted_symbol_replay_selects_historical_current_and_near():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    ts = datetime(2026, 1, 5, 9, 15)
    rows = [
        CashFutureHistory(symbol="TEST", contract_month="JAN", timestamp=ts,
                          expiry_date=date(2026, 1, 27), cash_price=100, future_price=103,
                          gap=3, gap_pct=3, lot_size=10, margin_required=1000),
        CashFutureHistory(symbol="TEST", contract_month="FEB", timestamp=ts,
                          expiry_date=date(2026, 2, 24), cash_price=100, future_price=105,
                          gap=5, gap_pct=5, lot_size=10, margin_required=1000),
        CashFutureHistory(symbol="TEST", contract_month="MAR", timestamp=ts,
                          expiry_date=date(2026, 3, 31), cash_price=100, future_price=107,
                          gap=7, gap_pct=7, lot_size=10, margin_required=1000),
        CashFutureHistory(symbol="TEST", contract_month="JAN", timestamp=ts + timedelta(minutes=1),
                          expiry_date=date(2026, 1, 27), cash_price=101, future_price=104,
                          gap=3, gap_pct=2.97, lot_size=10, margin_required=1000),
        CashFutureHistory(symbol="TEST", contract_month="FEB", timestamp=ts + timedelta(minutes=1),
                          expiry_date=date(2026, 2, 24), cash_price=101, future_price=106,
                          gap=5, gap_pct=4.95, lot_size=10, margin_required=1000),
    ]
    db.add_all(rows)
    db.commit()
    try:
        replay = list(iter_persisted_symbol_replay(db, "TEST", ts, ts + timedelta(minutes=1)))
        assert len(replay) == 2
        assert replay[0].current_expiry == date(2026, 1, 27)
        assert replay[0].near_expiry == date(2026, 2, 24)
        assert replay[0].current_gap == 3
        assert replay[0].near_gap == 5
    finally:
        db.query(CashFutureHistory).filter(CashFutureHistory.symbol == "TEST").delete()
        db.commit()
        db.close()
