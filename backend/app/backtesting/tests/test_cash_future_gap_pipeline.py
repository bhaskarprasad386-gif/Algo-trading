import pytest

from app.backtesting.cash_future_data_coverage import CashFutureDataCoverageAudit
from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalFetchRequest, HistoricalRecord
from app.backtesting.session_gap_planner import SessionAwareGapPlanner, SessionWindow


def record(ts: int) -> HistoricalRecord:
    return HistoricalRecord("angelone", "NSE:1:NIFTY", "1m", ts, {"close": 100.0})


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

    assert [(gap.start_ns, gap.end_ns) for gap in gaps] == [(180, 180)]
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
