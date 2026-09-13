import pytest

from app.backtesting.cash_future_data_coverage import CashFutureDataCoverageAudit
from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue
from app.backtesting.cash_future_gap_download import CashFutureGapDownloadPlanner
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.session_gap_planner import SessionAwareGapPlanner, SessionWindow


def record(ts: int, instrument: str = "NSE:1:NIFTY") -> HistoricalRecord:
    return HistoricalRecord("angelone", instrument, "1m", ts, {"close": 100.0})


def test_session_gap_planner_clips_repairs_to_expected_market_sessions():
    catalog = HistoricalCatalog()
    catalog.ingest([record(60), record(120), record(300), record(360)])

    planner = SessionAwareGapPlanner(catalog)
    gaps = planner.plan(
        source="angelone",
        instrument="NSE:1:NIFTY",
        timeframe="1m",
        interval_ns=60,
        sessions=(
            SessionWindow(0, 180),
            SessionWindow(300, 420),
        ),
    )

    assert [(gap.start_ns, gap.end_ns) for gap in gaps] == []
    catalog.close()


def test_coverage_audit_requires_complete_expected_timestamps():
    catalog = HistoricalCatalog()
    catalog.ingest([record(60), record(120)])
    queue = CashFutureDownloadQueue(
        spot=HistoricalFetchRequest("angelone", "NSE:1:NIFTY", "1m", 60, 180),
        futures=(),
    )
    audit = CashFutureDataCoverageAudit(catalog)

    report = audit.audit(
        queue=queue,
        mode="SPOT",
        interval_ns=60,
        spot_sessions=(SessionWindow(60, 180),),
    )

    assert not report.complete
    assert report.total_chunks == 1
    assert report.incomplete_chunks == 1
    assert report.missing_timestamps == 1
    with pytest.raises(LookupError, match="coverage incomplete"):
        audit.require_complete(
            queue=queue,
            mode="SPOT",
            interval_ns=60,
            spot_sessions=(SessionWindow(60, 180),),
        )
    catalog.close()


def test_gap_download_planner_repairs_leading_middle_and_trailing_bars():
    catalog = HistoricalCatalog()
    catalog.ingest([record(120), record(240)])
    queue = CashFutureDownloadQueue(
        spot=HistoricalFetchRequest("angelone", "NSE:1:NIFTY", "1m", 60, 300),
        futures=(),
    )

    plan = CashFutureGapDownloadPlanner(interval_ns=60, max_request_ns=180).plan(
        queue=queue,
        catalog=catalog,
        spot_sessions=(SessionWindow(60, 300),),
    )

    assert [(r.start_ns, r.end_ns) for r in plan.requests] == [(60, 60), (180, 180), (300, 300)]
    catalog.close()


def test_gap_download_planner_skips_complete_data_and_ignores_overnight_gap():
    catalog = HistoricalCatalog()
    catalog.ingest([record(60), record(120), record(180), record(300), record(360)])
    queue = CashFutureDownloadQueue(
        spot=HistoricalFetchRequest("angelone", "NSE:1:NIFTY", "1m", 60, 360),
        futures=(),
    )

    plan = CashFutureGapDownloadPlanner(interval_ns=60, max_request_ns=180).plan(
        queue=queue,
        catalog=catalog,
        spot_sessions=(SessionWindow(60, 180), SessionWindow(300, 360)),
    )

    assert plan.requests == ()
    catalog.close()


def test_gap_download_planner_is_bounded_and_handles_exact_future_tokens():
    catalog = HistoricalCatalog()
    future = "NFO:123:NIFTY26SEP"
    catalog.ingest([record(60, future), record(240, future)])
    queue = CashFutureDownloadQueue(
        spot=HistoricalFetchRequest("angelone", "NSE:1:NIFTY", "1m", 60, 60),
        futures=(
            type("FutureDownload", (), {
                "request": HistoricalFetchRequest("angelone", future, "1m", 60, 360)
            })(),
        ),
    )

    plan = CashFutureGapDownloadPlanner(interval_ns=60, max_request_ns=120).plan(
        queue=queue,
        catalog=catalog,
        spot_sessions=(SessionWindow(60, 60),),
        future_sessions={future: (SessionWindow(60, 360),)},
    )

    assert [(r.instrument, r.start_ns, r.end_ns) for r in plan.requests] == [
        (future, 120, 180),
        (future, 300, 360),
        ("NSE:1:NIFTY", 60, 60),
    ]
    assert all(r.end_ns - r.start_ns < 120 for r in plan.requests)
    catalog.close()
