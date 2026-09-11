from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future_catalog_materializer import materialize_cash_future_history


def test_materializer_streams_synchronized_rows_and_skips_unmatched_timestamps():
    catalog = HistoricalCatalog()
    t0 = int(datetime(2026, 1, 2, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    catalog.ingest([
        HistoricalRecord("angelone", "NSE:123:ABC-EQ", "1m", t0, {"close": 100}),
        HistoricalRecord("angelone", "NSE:123:ABC-EQ", "1m", t0 + 60_000_000_000, {"close": 101}),
        HistoricalRecord("angelone", "NSE:123:ABC-EQ", "1m", t0 + 120_000_000_000, {"close": 102}),
        HistoricalRecord("angelone", "NFO:999:ABC26JAN", "1m", t0, {"close": 105}),
        HistoricalRecord("angelone", "NFO:999:ABC26JAN", "1m", t0 + 120_000_000_000, {"close": 107}),
    ])

    engine = create_engine("sqlite:///:memory:")
    from app.core.database import Base
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        inserted = materialize_cash_future_history(
            db,
            catalog,
            source="angelone",
            spot_instrument="NSE:123:ABC-EQ",
            future_instrument="NFO:999:ABC26JAN",
            symbol="ABC",
            contract_month="2026-01",
            lot_size=100,
            expiry_date=datetime(2026, 1, 29).date(),
            batch_size=1,
        )
        rows = db.scalars(select(CashFutureHistory).order_by(CashFutureHistory.timestamp)).all()

    assert inserted == 2
    assert [(row.cash_price, row.future_price, row.gap) for row in rows] == [
        (100.0, 105.0, 5.0),
        (102.0, 107.0, 5.0),
    ]
    assert all(row.lot_size == 100 for row in rows)
    catalog.close()
