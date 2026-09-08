from datetime import datetime, timezone

from app.backtesting.cash_future_data_coverage import CashFutureDataCoverageAudit
from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue, CashFutureSegmentDownload
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.session_gap_planner import SessionWindow


def _ns(minute: int) -> int:
    return int(datetime(2026, 1, 5, 9, 15 + minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_audit_reports_missing_stored_timestamps_without_mutating_catalog():
    catalog = HistoricalCatalog(":memory:")
    spot_request = HistoricalFetchRequest("test", "NSE:SPOT", "1m", _ns(0), _ns(1))
    future_request = HistoricalFetchRequest("test", "NFO:FUT", "1m", _ns(0), _ns(1))
    catalog.ingest(
        (
            HistoricalRecord("test", "NSE:SPOT", "1m", _ns(0), {"close": 100}),
            HistoricalRecord("test", "NFO:FUT", "1m", _ns(0), {"close": 101}),
        )
    )
    queue = CashFutureDownloadQueue(
        spot=spot_request,
        futures=(CashFutureSegmentDownload(segment=None, request=future_request),),
    )
    session = SessionWindow(_ns(0), _ns(1))

    audit = CashFutureDataCoverageAudit(catalog).audit(
        queue=queue,
        mode="CURRENT",
        interval_ns=60_000_000_000,
        spot_sessions=(session,),
        future_sessions={future_request.instrument: (session,)},
    )

    assert not audit.complete
    assert audit.total_chunks == 2
    assert audit.complete_chunks == 0
    assert audit.incomplete_chunks == 2
    assert audit.missing_timestamps == 2
    assert len(catalog.timestamps(source="test", instrument="NSE:SPOT", timeframe="1m", start_ns=_ns(0), end_ns=_ns(1))) == 1
