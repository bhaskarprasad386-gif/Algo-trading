from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.cash_future_universe_acquisition import (
    CashFutureUniverseAcquisitionResult,
    acquire_cash_future_universe,
)
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_job_store import HistoricalJobStore
from app.scanner.cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from app.scanner.session_gap_planner import SessionWindow


def test_requires_durable_run_id_pair():
    with pytest.raises(ValueError, match="job_store and run_id"):
        acquire_cash_future_universe(
            service=object(),
            universe=CashFutureFnoUniverse((), ()),
            master_rows=(),
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            spot_sessions_by_underlying={},
            job_store=object(),
        )


def test_rejects_reversed_date_range():
    with pytest.raises(ValueError, match="end must not precede start"):
        acquire_cash_future_universe(
            service=object(),
            universe=CashFutureFnoUniverse((), ()),
            master_rows=(),
            start=datetime(2026, 1, 2, tzinfo=timezone.utc),
            end=datetime(2026, 1, 1, tzinfo=timezone.utc),
            spot_sessions_by_underlying={},
        )


def test_pending_chunks_excludes_completed_and_skipped_chunks():
    result = CashFutureUniverseAcquisitionResult(
        (
            SimpleNamespace(
                plan=SimpleNamespace(requests=(1, 2, 3, 4)),
                execution=SimpleNamespace(
                    completed_chunks=2,
                    skipped_chunks=1,
                    processed_chunks=3,
                ),
                coverage=SimpleNamespace(status="INCOMPLETE"),
            ),
        )
    )

    assert result.completed_chunks == 2
    assert result.pending_chunks == 1
    assert len(result.incomplete) == 1


def test_orchestrates_each_stock_with_master_cash_and_exact_future_sessions():
    start = datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc)
    end = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)
    session = SessionWindow(
        start_ns=int(start.timestamp() * 1_000_000_000),
        end_ns=int(end.timestamp() * 1_000_000_000),
    )
    universe = CashFutureFnoUniverse(
        stocks=(
            CashFutureUniverseItem(
                underlying="ABC",
                contract_month="2026-10",
                future_token="999",
                future_symbol="ABC26OCT",
                expiry=date(2026, 10, 29),
                lot_size=125,
            ),
        ),
        indices=(),
    )
    master_rows = (
        {
            "exch_seg": "NSE",
            "symbol": "ABC-EQ",
            "name": "ABC",
            "token": "123",
            "instrumenttype": "",
        },
    )

    calls = []

    class FakeService:
        @staticmethod
        def _session_days(sessions):
            return (date(2026, 9, 10),)

        def acquire(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                plan=SimpleNamespace(requests=(1, 2)),
                execution=SimpleNamespace(completed_chunks=2, processed_chunks=2),
                coverage=SimpleNamespace(status="READY"),
            )

    result = acquire_cash_future_universe(
        service=FakeService(),
        universe=universe,
        master_rows=master_rows,
        start=start,
        end=end,
        spot_sessions_by_underlying={"ABC": (session,)},
        future_sessions_by_instrument={"NFO:999:ABC26OCT": (session,)},
    )

    assert len(calls) == 1
    assert calls[0]["underlying"] == "ABC"
    assert calls[0]["spot_instrument"] == "NSE:123:ABC-EQ"
    assert calls[0]["queue"].spot.instrument == "NSE:123:ABC-EQ"
    assert tuple(request.instrument for request in calls[0]["queue"].futures) == (
        "NFO:999:ABC26OCT",
    )
    assert calls[0]["future_sessions"]["NFO:999:ABC26OCT"] == (session,)
    assert result.completed_chunks == 2
    assert result.pending_chunks == 0
    assert result.incomplete == ()


class _MultiStockHistoricalSource:
    def __init__(self):
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        timestamp = request.start_ns
        while timestamp <= request.end_ns:
            yield HistoricalRecord(
                request.source,
                request.instrument,
                request.timeframe,
                timestamp,
                {"close": 100.0},
            )
            timestamp += 60 * 1_000_000_000


def test_multi_stock_multi_contract_acquisition_is_durable_and_idempotent(tmp_path):
    start = datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc)
    end = datetime(2026, 9, 10, 9, 17, tzinfo=timezone.utc)
    session = SessionWindow(
        int(start.timestamp() * 1_000_000_000),
        int(end.timestamp() * 1_000_000_000),
    )

    contract_master = ContractMasterCatalog(tmp_path / "contracts.db")
    contract_master.upsert_snapshot(
        date(2026, 9, 1),
        [
            ContractRecord("NFO", "ABC26OCT", "999", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 125),
            ContractRecord("NFO", "ABC26NOV", "998", date(2026, 11, 26), "STOCK_FUTURE", "ABC", 125),
            ContractRecord("NFO", "XYZ26OCT", "997", date(2026, 10, 29), "STOCK_FUTURE", "XYZ", 75),
        ],
    )
    universe = CashFutureFnoUniverse(
        stocks=(
            CashFutureUniverseItem("ABC", "2026-10", "999", "ABC26OCT", date(2026, 10, 29), 125),
            CashFutureUniverseItem("ABC", "2026-11", "998", "ABC26NOV", date(2026, 11, 26), 125),
            CashFutureUniverseItem("XYZ", "2026-10", "997", "XYZ26OCT", date(2026, 10, 29), 75),
        ),
        indices=(),
    )
    master_rows = (
        {"exch_seg": "NSE", "symbol": "ABC-EQ", "name": "ABC", "token": "123", "instrumenttype": ""},
        {"exch_seg": "NSE", "symbol": "XYZ-EQ", "name": "XYZ", "token": "456", "instrumenttype": ""},
    )
    source = _MultiStockHistoricalSource()
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    service = CashFutureHistoricalAcquisitionService(
        ingestion,
        source,
        contract_master,
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
        sleep=lambda _: None,
    )
    job_store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    sessions = {"ABC": (session,), "XYZ": (session,)}
    future_sessions = {
        "NFO:999:ABC26OCT": (session,),
        "NFO:998:ABC26NOV": (session,),
        "NFO:997:XYZ26OCT": (session,),
    }

    first = acquire_cash_future_universe(
        service=service,
        universe=universe,
        master_rows=master_rows,
        start=start,
        end=end,
        spot_sessions_by_underlying=sessions,
        future_sessions_by_instrument=future_sessions,
        mode="CURRENT",
        retry_attempts=1,
        job_store=job_store,
        run_id="run-1",
        job_id_prefix="cash-future-test",
    )

    first_request_count = len(source.requests)
    assert first.incomplete == ()
    assert first.pending_chunks == 0
    assert first.completed_chunks > 0
    assert {request.instrument for request in source.requests} == {
        "NSE:123:ABC-EQ",
        "NFO:999:ABC26OCT",
        "NFO:998:ABC26NOV",
        "NSE:456:XYZ-EQ",
        "NFO:997:XYZ26OCT",
    }
    assert history.timestamps(
        source="angelone",
        instrument="NSE:123:ABC-EQ",
        timeframe="1m",
        start_ns=session.start_ns,
        end_ns=session.end_ns,
    )
    assert history.timestamps(
        source="angelone",
        instrument="NSE:456:XYZ-EQ",
        timeframe="1m",
        start_ns=session.start_ns,
        end_ns=session.end_ns,
    )

    second = acquire_cash_future_universe(
        service=service,
        universe=universe,
        master_rows=master_rows,
        start=start,
        end=end,
        spot_sessions_by_underlying=sessions,
        future_sessions_by_instrument=future_sessions,
        mode="CURRENT",
        retry_attempts=1,
        job_store=job_store,
        run_id="run-1",
        job_id_prefix="cash-future-test",
    )

    assert second.incomplete == ()
    assert second.pending_chunks == 0
    assert len(source.requests) == first_request_count
