from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.backtesting.angelone_cash_future_batch_runner import (
    run_angelone_cash_future_history_in_batches,
)
from app.backtesting.angelone_cash_future_runner import AngelOneCashFutureRunConfig
from app.backtesting.cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.session_gap_planner import SessionWindow
from app.core.database import Base
from app.models.cash_future_history import CashFutureHistory


MARKET_TZ = ZoneInfo("Asia/Kolkata")


class FakeClient:
    def getCandleData(self, params):
        token = params["symboltoken"]
        base = 100.0 if token in {"101", "1001"} else 200.0
        future = token.startswith("2") or token == "2001"
        premium = 5.0 if future else 0.0
        return {
            "status": True,
            "data": [
                ["2026-01-05 09:15", base + premium, base + premium + 1, base + premium - 1, base + premium + 0.5, 1000, 250],
                ["2026-01-05 09:16", base + premium + 0.5, base + premium + 2, base + premium, base + premium + 1.5, 1200, 275],
                ["2026-01-06 09:15", base + premium + 1.0, base + premium + 2, base + premium, base + premium + 1.5, 1100, 260],
                ["2026-01-06 09:16", base + premium + 1.5, base + premium + 3, base + premium + 1, base + premium + 2.5, 1300, 280],
            ],
        }


class FakeAuth:
    def __init__(self):
        self.client = FakeClient()

    def get_client(self):
        return self.client


class FakeLimiter:
    def __init__(self):
        self.calls = 0

    def acquire(self):
        self.calls += 1


def _ns(local: datetime) -> int:
    return int(local.replace(tzinfo=MARKET_TZ).timestamp() * 1_000_000_000)


def _sessions():
    return tuple(
        SessionWindow(_ns(datetime.combine(day, time(9, 15))), _ns(datetime.combine(day, time(9, 16))))
        for day in (date(2026, 1, 5), date(2026, 1, 6))
    )


def test_real_angelone_batch_path_downloads_two_stocks_across_two_days_and_materializes():
    contracts = ContractMasterCatalog()
    snapshot = date(2026, 1, 5)
    contracts.upsert_snapshot(
        snapshot,
        [
            ContractRecord("NFO", "AAA26JANFUT", "2001", date(2026, 1, 29), "STOCK_FUTURE", "AAA", 100),
            ContractRecord("NFO", "BBB26JANFUT", "2002", date(2026, 1, 29), "STOCK_FUTURE", "BBB", 50),
        ],
    )

    universe = CashFutureFnoUniverse(
        stocks=(
            CashFutureUniverseItem("AAA", "2026-01", "2001", "AAA26JANFUT", date(2026, 1, 29), 100),
            CashFutureUniverseItem("BBB", "2026-01", "2002", "BBB26JANFUT", date(2026, 1, 29), 50),
        ),
        indices=(),
    )
    master_rows = (
        {"exch_seg": "NSE", "symbol": "AAA-EQ", "name": "AAA", "token": "101", "instrumenttype": "EQ"},
        {"exch_seg": "NSE", "symbol": "BBB-EQ", "name": "BBB", "token": "102", "instrumenttype": "EQ"},
    )
    sessions = _sessions()
    spot_sessions = {"AAA": sessions, "BBB": sessions}
    future_sessions = {
        "NFO:2001:AAA26JANFUT": sessions,
        "NFO:2002:BBB26JANFUT": sessions,
    }

    catalog = HistoricalCatalog()
    ingestion = HistoricalIngestionService(catalog)
    job_store = HistoricalJobStore()
    limiter = FakeLimiter()
    config = AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
        timeframe="1m",
        mode="CURRENT",
        chunk_days=30,
        retry_attempts=2,
        max_repair_passes=2,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    try:
        with Session(engine) as db:
            results = run_angelone_cash_future_history_in_batches(
                ingestion=ingestion,
                contract_master=contracts,
                universe=universe,
                master_rows=master_rows,
                start=datetime(2026, 1, 5, 9, 15),
                end=datetime(2026, 1, 6, 9, 16),
                spot_sessions_by_underlying=spot_sessions,
                future_sessions_by_instrument=future_sessions,
                db=db,
                catalog=catalog,
                config=config,
                batch_size=1,
                job_store=job_store,
                run_id="integration-multi-stock-multi-day",
                auth=FakeAuth(),
                limiter=limiter,
            )

            assert tuple(result.stock_underlyings for result in results) == (("AAA",), ("BBB",))
            assert all(not result.skipped for result in results)
            assert all(result.result is not None and result.result.backtest_ready for result in results)

            rows = db.scalars(select(CashFutureHistory).order_by(CashFutureHistory.symbol, CashFutureHistory.timestamp)).all()
            assert len(rows) == 8
            assert {row.symbol for row in rows} == {"AAA", "BBB"}
            assert {row.contract_month for row in rows} == {"2026-01"}
            assert all(row.future_price > row.cash_price for row in rows)
            assert catalog.count(source="angelone", timeframe="1m") == 16
            assert limiter.calls >= 4
    finally:
        job_store.close()
        catalog.close()
        contracts.close()
        engine.dispose()
