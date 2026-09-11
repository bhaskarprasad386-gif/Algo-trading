from datetime import date, datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.backtesting.cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from app.backtesting.cash_future_universe_download_plan import build_cash_future_universe_download_plan
from app.backtesting.cash_future_universe_materializer import materialize_cash_future_universe_history
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.core.database import Base
from app.models.cash_future_history import CashFutureHistory


IST = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")


def test_materializes_each_contract_without_cross_contract_mixing():
    catalog = HistoricalCatalog()
    t0 = int(datetime(2026, 10, 1, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    catalog.ingest([
        HistoricalRecord("angelone", "NSE:11:ABC-EQ", "1m", t0, {"close": 100}),
        HistoricalRecord("angelone", "NSE:11:ABC-EQ", "1m", t0 + 60_000_000_000, {"close": 101}),
        HistoricalRecord("angelone", "NFO:101:ABC26OCT", "1m", t0, {"close": 105}),
        HistoricalRecord("angelone", "NFO:101:ABC26OCT", "1m", t0 + 60_000_000_000, {"close": 106}),
        HistoricalRecord("angelone", "NFO:102:ABC26NOV", "1m", t0, {"close": 107}),
    ])
    universe = CashFutureFnoUniverse(stocks=(
        CashFutureUniverseItem("ABC", "2026-10", "101", "ABC26OCT", date(2026, 10, 29), 125),
        CashFutureUniverseItem("ABC", "2026-11", "102", "ABC26NOV", date(2026, 11, 26), 125),
    ), indices=())
    start = datetime(2026, 10, 1, 9, 15, tzinfo=IST)
    end = datetime(2026, 10, 1, 15, 30, tzinfo=IST)
    plan = build_cash_future_universe_download_plan(
        universe=universe,
        master_rows=({"exch_seg": "NSE", "name": "ABC", "symbol": "ABC-EQ", "token": "11", "instrumenttype": "EQ"},),
        start=start,
        end=end,
        session_days=(date(2026, 10, 1),),
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        inserted = materialize_cash_future_universe_history(db, catalog, download_plan=plan, universe=universe, batch_size=1)
        rows = db.scalars(select(CashFutureHistory).order_by(CashFutureHistory.contract_month, CashFutureHistory.timestamp)).all()
    assert inserted == 3
    assert [(row.contract_month, row.future_price) for row in rows] == [("2026-10", 105.0), ("2026-10", 106.0), ("2026-11", 107.0)]
    assert all(row.lot_size == 125 for row in rows)
    catalog.close()
