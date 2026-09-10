from datetime import datetime, timezone

from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.cash_future_rollover_plan import build_mode_segments
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.session_gap_planner import SessionWindow


class Source:
    def fetch(self, request):
        return iter(())


def test_prepare_derives_india_local_session_days_from_spot_sessions(tmp_path, monkeypatch):
    captured = {}

    def fake_build_mode_segments(catalog, **kwargs):
        captured.update(kwargs)
        return ((), ())

    monkeypatch.setattr(
        "app.backtesting.cash_future_download_queue.build_mode_segments",
        fake_build_mode_segments,
    )

    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        datetime(2026, 1, 1, tzinfo=timezone.utc).date(),
        [ContractRecord("NFO", "SBINJAN", "101", datetime(2026, 1, 29, tzinfo=timezone.utc).date(), "STOCK_FUTURE", "SBIN", 750)],
    )
    history = HistoricalCatalog(tmp_path / "history.db")
    service = CashFutureHistoricalAcquisitionService(
        HistoricalIngestionService(history),
        Source(),
        catalog,
        interval_ns=60 * 1_000_000_000,
        max_request_ns=120 * 1_000_000_000,
    )

    session = SessionWindow(
        int(datetime(2026, 9, 7, 3, 45, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
        int(datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
    )
    service.prepare(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=datetime(2026, 9, 7, tzinfo=timezone.utc),
        end=datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc),
        spot_sessions=(session,),
        future_sessions={},
        mode="CURRENT",
    )

    assert captured["session_days"] == (datetime(2026, 9, 7, tzinfo=timezone.utc).astimezone(timezone.utc).date(),)
