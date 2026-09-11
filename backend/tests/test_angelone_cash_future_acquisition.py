from app.backtesting.angelone_cash_future_acquisition import (
    build_angelone_cash_future_acquisition_service,
)
from app.backtesting.angelone_historical import AngelOneHistoricalSource
from app.backtesting.cash_future_historical_acquisition import (
    CashFutureHistoricalAcquisitionService,
)


class FakeAuth:
    def get_client(self):
        raise AssertionError("network client must not be used by the wiring test")


class FakeLimiter:
    def acquire(self):
        raise AssertionError("rate limiter must not be used by the wiring test")


def test_builds_real_angelone_source_into_cash_future_service():
    service = build_angelone_cash_future_acquisition_service(
        object(),
        object(),
        interval_ns=60_000_000_000,
        max_request_ns=30 * 86_400 * 1_000_000_000,
        auth=FakeAuth(),
        limiter=FakeLimiter(),
        chunk_days=30,
    )

    assert isinstance(service, CashFutureHistoricalAcquisitionService)
    assert isinstance(service.source, AngelOneHistoricalSource)
    assert service.source.source_name == "angelone"
    assert service.source.auth.__class__ is FakeAuth
    assert service.source.limiter.__class__ is FakeLimiter
    assert service.source.chunk_days == 30
