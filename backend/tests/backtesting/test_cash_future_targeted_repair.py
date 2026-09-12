from types import SimpleNamespace

from app.backtesting.cash_future_historical_download import CashFutureHistoricalDownloadService
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_status import DownloadChunkStatus, HistoricalDownloadStatusStore
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.session_gap_planner import SessionWindow


def test_missing_runs_are_collapsed_to_minimal_requests(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "catalog.db"))
    service = CashFutureHistoricalDownloadService(
        catalog,
        object(),
        source=SimpleNamespace(source_name="angelone"),
        session_windows=lambda request: (SessionWindow(0, 5),),
    )
    job = SimpleNamespace(timeframe="1m")
    chunk = SimpleNamespace(instrument="NSE:1:AAA", start_ns=0, end_ns=5)
    interval = 60 * 1_000_000_000
    for timestamp in (0, 60_000_000_000, 180_000_000_000, 240_000_000_000, 300_000_000_000):
        catalog.ingest((HistoricalRecord("angelone", chunk.instrument, "1m", timestamp, {"close": 100}),))
    # Session boundaries are expressed in nanoseconds; use a second service window for this test.
    service.session_windows = lambda request: (SessionWindow(0, 5 * interval),)
    requests = service._missing_requests_for_chunk(job, chunk)
    assert requests == (HistoricalFetchRequest("angelone", chunk.instrument, "1m", 120_000_000_000, 120_000_000_000),)


def test_missing_runs_helper_keeps_separated_ranges_separate():
    interval = 60 * 1_000_000_000
    assert CashFutureHistoricalDownloadService._missing_runs(
        {0, interval, 3 * interval, 4 * interval}, interval
    ) == ((0, interval), (3 * interval, 4 * interval))


def test_repair_plan_is_empty_after_chunk_becomes_complete(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "catalog.db"))
    status = HistoricalDownloadStatusStore(str(tmp_path / "status.db"))
    service = CashFutureHistoricalDownloadService(
        catalog,
        object(),
        source=SimpleNamespace(source_name="angelone"),
        status_store=status,
        session_windows=lambda request: (SessionWindow(0, 2 * 60 * 1_000_000_000),),
    )
    status.create_job(
        job_id="repair-complete",
        source="angelone",
        mode="BOTH",
        timeframe="1m",
        spot_instrument="NSE:1:AAA",
        exchange="NSE",
        underlying="AAA",
        start_ns=0,
        end_ns=2 * 60 * 1_000_000_000,
        requested_chunks=1,
    )
    for timestamp in (0, 60_000_000_000, 120_000_000_000):
        catalog.ingest((HistoricalRecord("angelone", "NSE:1:AAA", "1m", timestamp, {"close": 100}),))
    status.upsert_chunk(
        DownloadChunkStatus(
            job_id="repair-complete",
            sequence=0,
            instrument="NSE:1:AAA",
            start_ns=0,
            end_ns=2 * 60 * 1_000_000_000,
            status="COMPLETE",
            attempts=1,
            expected_timestamps=3,
            actual_timestamps=3,
            missing_timestamps=0,
        )
    )
    plan, sequences = service._resume_plan("repair-complete")
    assert plan.requests == ()
    assert sequences == ()
