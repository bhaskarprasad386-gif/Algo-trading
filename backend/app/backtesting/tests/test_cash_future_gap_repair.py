from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue, CashFutureSegmentDownload
from app.backtesting.cash_future_gap_repair import CashFutureGapRepairPlanner
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.session_gap_planner import SessionWindow


def record(ts: int, instrument: str = "NSE:1:NIFTY") -> HistoricalRecord:
    return HistoricalRecord("angelone", instrument, "1m", ts, {"close": 100.0})


def test_gap_repair_plan_is_session_bounded_and_deterministic():
    catalog = HistoricalCatalog()
    catalog.ingest([record(60), record(120), record(300), record(360)])
    queue = CashFutureDownloadQueue(
        spot=HistoricalFetchRequest("angelone", "NSE:1:NIFTY", "1m", 60, 420),
        futures=(),
    )

    plan = CashFutureGapRepairPlanner(catalog).plan(
        queue=queue,
        interval_ns=60,
        spot_sessions=(SessionWindow(0, 180), SessionWindow(300, 420)),
    )

    assert [(r.start_ns, r.end_ns) for r in plan.spot] == [(180, 180), (420, 420)]
    assert plan.total_requests == 2
    catalog.close()


def test_gap_repair_plan_handles_future_specific_sessions():
    catalog = HistoricalCatalog()
    catalog.ingest([record(60), record(300, "NSE:2:NIFTYFUT")])
    future = HistoricalFetchRequest("angelone", "NSE:2:NIFTYFUT", "1m", 60, 360)
    queue = CashFutureDownloadQueue(
        spot=HistoricalFetchRequest("angelone", "NSE:1:NIFTY", "1m", 60, 360),
        futures=(CashFutureSegmentDownload(segment=None, request=future),),
    )

    plan = CashFutureGapRepairPlanner(catalog).plan(
        queue=queue,
        interval_ns=60,
        spot_sessions=(SessionWindow(60, 60),),
        future_sessions={future.instrument: (SessionWindow(60, 180), SessionWindow(300, 360))},
    )

    assert [(r.instrument, r.start_ns, r.end_ns) for r in plan.futures] == [
        (future.instrument, 60, 180),
        (future.instrument, 360, 360),
    ]
    catalog.close()
