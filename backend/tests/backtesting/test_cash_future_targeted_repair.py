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


def test_targeted_repair_preserves_parent_chunk_identity_and_reconciles_metrics(tmp_path):
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
        job_id="repair-parent",
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
    catalog.ingest((HistoricalRecord("angelone", "NSE:1:AAA", "1m", 0, {"close": 100}),))
    status.upsert_chunk(
        DownloadChunkStatus(
            job_id="repair-parent",
            sequence=0,
            instrument="NSE:1:AAA",
            start_ns=0,
            end_ns=2 * 60 * 1_000_000_000,
            status="RUNNING",
            attempts=1,
            expected_timestamps=3,
            actual_timestamps=1,
            missing_timestamps=2,
            first_missing_ns=60_000_000_000,
        )
    )
    callbacks = service._callbacks("repair-parent", sequence_numbers=(0,))
    repair_request = HistoricalFetchRequest("angelone", "NSE:1:AAA", "1m", 60_000_000_000, 60_000_000_000)
    callbacks["on_chunk_start"](0, repair_request, 2)
    catalog.ingest((HistoricalRecord("angelone", "NSE:1:AAA", "1m", 60_000_000_000, {"close": 101}),))
    callbacks["on_chunk_complete"](0, repair_request, SimpleNamespace(fetched=1, inserted=1), 2)

    parent = status.chunks("repair-parent")[0]
    assert (parent.start_ns, parent.end_ns) == (0, 2 * 60 * 1_000_000_000)
    assert parent.missing_timestamps == 1
    assert parent.status == "RUNNING"

    catalog.ingest((HistoricalRecord("angelone", "NSE:1:AAA", "1m", 120_000_000_000, {"close": 102}),))
    callbacks["on_chunk_complete"](0, repair_request, SimpleNamespace(fetched=1, inserted=1), 3)
    parent = status.chunks("repair-parent")[0]
    assert (parent.start_ns, parent.end_ns) == (0, 2 * 60 * 1_000_000_000)
    assert parent.missing_timestamps == 0
    assert parent.status == "COMPLETE"
    assert parent.fetched_records == 2
    assert parent.inserted_records == 2
