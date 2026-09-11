from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.backtesting import angelone_cash_future_runner as runner
from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue
from app.backtesting.cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalFetchRequest, HistoricalRecord
from app.core.database import Base
from app.models.cash_future_history import CashFutureHistory


class FakeAuth:
    def __init__(self):
        self.calls = []

    def get_client(self):
        self.calls.append("get_client")
        return object()


def test_runner_config_rejects_request_window_over_one_day():
    with pytest.raises(ValueError, match="must not exceed one day"):
        runner.AngelOneCashFutureRunConfig(
            interval_ns=60_000_000_000,
            max_request_ns=86_400_000_000_001,
        )


def test_runner_authenticates_before_starting_acquisition(monkeypatch):
    auth = FakeAuth()
    service_marker = object()
    pipeline_calls = {}

    def fake_builder(ingestion, contract_master, **kwargs):
        pipeline_calls["builder"] = kwargs
        return service_marker

    def fake_pipeline(**kwargs):
        pipeline_calls["pipeline"] = kwargs
        return "result"

    monkeypatch.setattr(runner, "build_angelone_cash_future_acquisition_service", fake_builder)
    monkeypatch.setattr(runner, "acquire_and_materialize_cash_future_universe", fake_pipeline)

    config = runner.AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
    )
    db = object()
    catalog = object()

    result = runner.run_angelone_cash_future_history(
        ingestion=object(),
        contract_master=object(),
        universe=object(),
        master_rows=(),
        start=datetime(2026, 1, 5, 9, 15),
        end=datetime(2026, 1, 5, 15, 30),
        spot_sessions_by_underlying={},
        db=db,
        catalog=catalog,
        config=config,
        auth=auth,
    )

    assert result == "result"
    assert auth.calls == ["get_client"]
    assert pipeline_calls["builder"]["interval_ns"] == config.interval_ns
    assert pipeline_calls["builder"]["max_request_ns"] == config.max_request_ns
    assert pipeline_calls["pipeline"]["service"] is service_marker
    assert pipeline_calls["pipeline"]["db"] is db
    assert pipeline_calls["pipeline"]["catalog"] is catalog
    assert pipeline_calls["pipeline"]["timeframe"] == "1m"
    assert pipeline_calls["pipeline"]["mode"] == "BOTH"


def test_runner_materializes_catalog_rows_into_cash_future_history(monkeypatch):
    start_ns = int(datetime(2026, 10, 1, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    cash = HistoricalFetchRequest("angelone", "NSE:11:ABC-EQ", "1m", start_ns, start_ns)
    future = HistoricalFetchRequest("angelone", "NFO:101:ABC26OCT", "1m", start_ns, start_ns)
    queue = CashFutureDownloadQueue(cash, (future,))

    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("angelone", cash.instrument, "1m", start_ns, {"close": 100.0}),
        HistoricalRecord("angelone", future.instrument, "1m", start_ns, {"close": 105.0}),
    ])
    universe = CashFutureFnoUniverse(
        stocks=(CashFutureUniverseItem("ABC", "2026-10", "101", "ABC26OCT", date(2026, 10, 29), 125),),
        indices=(),
    )
    acquisition = type("Acquisition", (), {"results": (type("Result", (), {"queue": queue, "coverage": type("Coverage", (), {"complete": True})()})(),)})()

    monkeypatch.setattr(runner, "build_angelone_cash_future_acquisition_service", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "acquire_cash_future_universe", lambda **kwargs: acquisition)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    auth = FakeAuth()
    config = runner.AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
    )

    with Session(engine) as db:
        result = runner.run_angelone_cash_future_history(
            ingestion=object(),
            contract_master=object(),
            universe=universe,
            master_rows=(),
            start=datetime(2026, 10, 1, 9, 15, tzinfo=timezone.utc),
            end=datetime(2026, 10, 1, 15, 30, tzinfo=timezone.utc),
            spot_sessions_by_underlying={},
            db=db,
            catalog=catalog,
            config=config,
            auth=auth,
        )
        rows = db.scalars(select(CashFutureHistory)).all()

    assert auth.calls == ["get_client"]
    assert result.materialized_rows == 1
    assert result.backtest_ready is True
    assert len(rows) == 1
    assert rows[0].symbol == "ABC"
    assert rows[0].cash_price == 100.0
    assert rows[0].future_price == 105.0
    catalog.close()
