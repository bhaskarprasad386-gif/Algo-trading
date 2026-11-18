import pytest

from app.backtesting.historical_rate_limiter import HistoricalRateLimiter, HistoricalRateLimit


def test_default_limits_match_documented_historical_endpoint():
    limiter = HistoricalRateLimiter()
    assert limiter.limit == HistoricalRateLimit(3, 150, 5000)


def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        HistoricalRateLimiter(HistoricalRateLimit(0, 150, 5000))
    with pytest.raises(ValueError):
        HistoricalRateLimiter(safety_delay_seconds=-1)
