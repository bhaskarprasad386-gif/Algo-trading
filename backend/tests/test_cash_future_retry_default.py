from datetime import datetime, timezone

import app.backtesting.cash_future_historical_acquisition as acquisition_module
from app.backtesting.cash_future_historical_acquisition import CashFutureHistoricalAcquisitionService
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.provider_retry import ProviderRetryPolicy
from app.backtesting.session_gap_planner import SessionWindow
from tests.test_cash_future_historical_acquisition import FakeHistoricalSource, _contract_catalog


def test_cash_future_acquisition_auto_builds_known_provider_retry_policy(tmp_path, monkeypatch):
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    source = FakeHistoricalSource()
    service = CashFutureHistoricalAcquisitionService(
        ingestion, source, _contract_catalog(),
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
        sleep=lambda _: None,
    )
    session = SessionWindow(
        int(datetime(2026, 1, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
        int(datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
    )
    calls = []
    policy = ProviderRetryPolicy(min_interval_seconds=0, jitter_ratio=0, sleeper=lambda _: None)

    def build_policy(provider):
        calls.append(provider)
        return policy

    monkeypatch.setattr(acquisition_module, "build_provider_retry_policy", build_policy)

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,), future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m", mode="CURRENT", source="AngelOne",
        retry_attempts=1, max_repair_passes=1,
    )

    assert calls == ["AngelOne"]
    assert result.execution.failed_request_index is None
    assert result.plan.requests == ()
    assert source.requests


def test_cash_future_acquisition_keeps_custom_sources_on_executor_defaults(tmp_path, monkeypatch):
    history = HistoricalCatalog(tmp_path / "history.db")
    ingestion = HistoricalIngestionService(history)
    source = FakeHistoricalSource()
    service = CashFutureHistoricalAcquisitionService(
        ingestion, source, _contract_catalog(),
        interval_ns=60 * 1_000_000_000,
        max_request_ns=2 * 60 * 1_000_000_000,
        sleep=lambda _: None,
    )
    session = SessionWindow(
        int(datetime(2026, 1, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
        int(datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
    )
    calls = []

    def build_policy(provider):
        calls.append(provider)
        raise AssertionError("custom providers must not require a built-in retry profile")

    monkeypatch.setattr(acquisition_module, "build_provider_retry_policy", build_policy)

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN", exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 1, 29, 0, 3, tzinfo=timezone.utc),
        spot_sessions=(session,), future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m", mode="CURRENT", source="custom-test-provider",
        retry_attempts=1, max_repair_passes=1,
    )

    assert calls == []
    assert result.execution.failed_request_index is None
    assert result.plan.requests == ()
