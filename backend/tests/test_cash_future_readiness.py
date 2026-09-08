from datetime import datetime, timezone

import pytest

from app.backtesting.cash_future_data_coverage import CashFutureDataCoverageAudit
from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue, CashFutureSegmentDownload
from app.backtesting.cash_future_readiness import CashFutureReadinessGate
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.session_gap_planner import SessionWindow

from .test_cash_future_contract_preflight import FakeCatalog


def _ns(minute: int) -> int:
    return int(datetime(2026, 1, 5, 9, 15 + minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_readiness_gate_is_incomplete_when_contracts_and_data_are_missing():
    historical = HistoricalCatalog(":memory:")
    spot = HistoricalFetchRequest("test", "NSE:SPOT", "1m", _ns(0), _ns(1))
    future = HistoricalFetchRequest("test", "NFO:FUT", "1m", _ns(0), _ns(1))
    queue = CashFutureDownloadQueue(
        spot=spot,
        futures=(CashFutureSegmentDownload(segment=None, request=future),),
    )
    session = SessionWindow(_ns(0), _ns(1))
    gate = CashFutureReadinessGate(
        contract_catalog=FakeCatalog(snapshots=()), historical_catalog=historical
    )

    report = gate.audit(
        exchange="NFO",
        underlying="ABC",
        end=datetime(2026, 9, 8),
        queue=queue,
        mode="CURRENT",
        interval_ns=60_000_000_000,
        spot_sessions=(session,),
        future_sessions={future.instrument: (session,)},
    )

    assert not report.complete
    assert report.missing_snapshot_dates
    assert report.data.incomplete_chunks == 2
    # Missing timestamps are aggregated across both spot and future legs.
    assert report.missing_timestamps == 4


def test_readiness_gate_fail_closed_with_combined_reason():
    historical = HistoricalCatalog(":memory:")
    request = HistoricalFetchRequest("test", "NSE:SPOT", "1m", _ns(0), _ns(1))
    queue = CashFutureDownloadQueue(spot=request, futures=())
    session = SessionWindow(_ns(0), _ns(1))
    gate = CashFutureReadinessGate(
        contract_catalog=FakeCatalog(snapshots=()), historical_catalog=historical
    )

    with pytest.raises(LookupError, match="Cash-Future backtest readiness incomplete"):
        gate.require_complete(
            exchange="NFO",
            underlying="ABC",
            end=datetime(2026, 9, 8),
            queue=queue,
            mode="CURRENT",
            interval_ns=60_000_000_000,
            spot_sessions=(session,),
        )
