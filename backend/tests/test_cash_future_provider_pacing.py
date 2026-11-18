from datetime import date, datetime, timezone

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.provider_retry import ProviderRetryPolicy
from app.backtesting.session_gap_planner import SessionWindow


class TwoChunkSource:
    def __init__(self):
        self.calls = 0

    def fetch(self, request):
        self.calls += 1
        yield HistoricalRecord(request.source, request.instrument, request.timeframe, request.start_ns, {"close": 100.0})


def test_multi_chunk_acquisition_enforces_provider_minimum_interval(tmp_path):
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
    ])
    history = HistoricalCatalog(tmp_path / "history.db")
    source = TwoChunkSource()
    service = CashFutureHistoricalAcquisitionService(
        HistoricalIngestionService(history), source, catalog,
        interval_ns=60 * 1_000_000_000,
        max_request_ns=60 * 1_000_000_000,
        sleep=lambda _: None,
    )
    start = datetime(2026, 1, 29, tzinfo=timezone.utc)
    end = datetime(2026, 1, 29, 0, 2, tzinfo=timezone.utc)
    session = SessionWindow(int(start.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000))
    sleeps = []
    policy = ProviderRetryPolicy(min_interval_seconds=0.5, jitter_ratio=0, sleeper=lambda seconds: sleeps.append(seconds))

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=start, end=end, spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,)}, timeframe="1m",
        mode="CURRENT", retry_attempts=1, retry_policy=policy, max_repair_passes=1,
    )

    # Three expected minute timestamps are planned for each leg: spot + future.
    assert result.execution.failed_request_index is None
    assert result.execution.completed_chunks == 6
    assert source.calls == 6
    assert len(sleeps) == 5
    assert all(0 < delay <= 0.5 for delay in sleeps)
