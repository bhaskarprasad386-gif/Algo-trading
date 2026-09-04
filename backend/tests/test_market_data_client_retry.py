from unittest.mock import patch

import pytest

from app.core.exceptions import TradingAppException
from app.market_data.client import MarketDataClient


class _AuthStub:
    def get_client(self):
        return object()


def test_retry_repeats_transient_failure_with_bounded_backoff():
    client = MarketDataClient(
        auth=_AuthStub(),
        max_retries=2,
        retry_backoff_seconds=0.25,
    )
    calls = []

    def operation():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("temporary network failure")
        return "ok"

    with patch("app.market_data.client.time.sleep") as sleep:
        assert client._request_with_retry("test", operation) == "ok"

    assert len(calls) == 3
    assert sleep.call_count == 2
    assert [call.args[0] for call in sleep.call_args_list] == [0.25, 0.5]


def test_retry_stops_after_configured_attempts():
    client = MarketDataClient(auth=_AuthStub(), max_retries=2, retry_backoff_seconds=0)
    calls = []

    def operation():
        calls.append(1)
        raise RuntimeError("still unavailable")

    with patch("app.market_data.client.time.sleep"):
        with pytest.raises(RuntimeError, match="still unavailable"):
            client._request_with_retry("test", operation)

    assert len(calls) == 3


def test_trading_app_exception_is_not_retried():
    client = MarketDataClient(auth=_AuthStub(), max_retries=3, retry_backoff_seconds=0.25)
    error = TradingAppException("BrokerError", "broker rejected request", 502)
    calls = []

    def operation():
        calls.append(1)
        raise error

    with patch("app.market_data.client.time.sleep") as sleep:
        with pytest.raises(TradingAppException) as exc_info:
            client._request_with_retry("test", operation)

    assert exc_info.value is error
    assert len(calls) == 1
    sleep.assert_not_called()


def test_retry_configuration_rejects_negative_values():
    with pytest.raises(ValueError, match="max_retries"):
        MarketDataClient(auth=_AuthStub(), max_retries=-1)

    with pytest.raises(ValueError, match="retry_backoff_seconds"):
        MarketDataClient(auth=_AuthStub(), retry_backoff_seconds=-0.1)
