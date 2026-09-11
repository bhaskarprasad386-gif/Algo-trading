from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue
from app.backtesting.cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from app.backtesting.cash_future_universe_pipeline import (
    CashFutureUniversePipelineResult,
    acquire_and_materialize_cash_future_universe,
)
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.core.database import Base
from app.models.cash_future_history import CashFutureHistory


def test_acquisition_result_queue_is_materialized_without_rebuilding_instruments(monkeypatch):
    start_ns = int(datetime(2026, 10, 1, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    cash = HistoricalFetchRequest("angelone", "NSE:11:ABC-EQ", "1m", start_ns, start_ns)
    future = HistoricalFetchRequest("angelone", "NFO:101:ABC26OCT", "1m", start_ns, start_ns)
    queue = CashFutureDownloadQueue(cash, (future,))
    acquisition = SimpleNamespace(results=(SimpleNamespace(queue=queue),))

    monkeypatch.setattr(
        "app.backtesting.cash_future_universe_pipeline.acquire_cash_future_universe",
        lambda **kwargs: acquisition,
    )

    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("angelone", cash.instrument, "1m", start_ns, {"close": 100.0}),
        HistoricalRecord("angelone", future.instrument, "1m", start_ns, {"close": 105.0}),
    ])
    universe = CashFutureFnoUniverse(
        stocks=(CashFutureUniverseItem("ABC", "2026-10", "101", "ABC26OCT", date(2026, 10, 29), 125),),
        indices=(),
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        result = acquire_and_materialize_cash_future_universe(
            service=object(),
            universe=universe,
            master_rows=(),
            start=datetime(2026, 10, 1, 9, 15, tzinfo=timezone.utc),
            end=datetime(2026, 10, 1, 15, 30, tzinfo=timezone.utc),
            spot_sessions_by_underlying={},
            db=db,
            catalog=catalog,
        )
        rows = db.scalars(select(CashFutureHistory)).all()

    assert result.materialized_rows == 1
    assert len(rows) == 1
    assert rows[0].symbol == "ABC"
    assert rows[0].contract_month == "2026-10"
    assert rows[0].cash_price == 100.0
    assert rows[0].future_price == 105.0
    catalog.close()


def test_pipeline_readiness_requires_materialization_and_every_acquisition_complete():
    complete = SimpleNamespace(complete=True)
    incomplete = SimpleNamespace(complete=False)

    ready = CashFutureUniversePipelineResult(
        SimpleNamespace(results=(SimpleNamespace(coverage=complete),)),
        materialized_rows=2,
    )
    blocked_incomplete = CashFutureUniversePipelineResult(
        SimpleNamespace(results=(SimpleNamespace(coverage=incomplete),)),
        materialized_rows=2,
    )
    blocked_empty = CashFutureUniversePipelineResult(
        SimpleNamespace(results=(SimpleNamespace(coverage=complete),)),
        materialized_rows=0,
    )

    assert ready.backtest_ready is True
    ready.require_backtest_ready()
    assert blocked_incomplete.backtest_ready is False
    assert blocked_empty.backtest_ready is False
    with pytest.raises(LookupError, match="backtest blocked"):
        blocked_incomplete.require_backtest_ready()
    with pytest.raises(LookupError, match="backtest blocked"):
        blocked_empty.require_backtest_ready()
