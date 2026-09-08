from datetime import datetime
import time
from typing import Any, Dict

from app.market_data.client import MarketDataClient
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class HistoricalDataClient:
    """Wrapper for Angel One historical candle data with bounded retry/backoff."""

    def __init__(
        self,
        market_client: MarketDataClient | None = None,
        *,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        sleep_fn: Any = time.sleep,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        self.market_client = market_client or MarketDataClient()
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.sleep_fn = sleep_fn

    def get_candles(
        self,
        exchange: str,
        symboltoken: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> Dict[str, Any]:
        """Fetch historical candle data from Angel One.

        Only transient request failures are retried. A successful HTTP/API response
        with ``status=False`` is surfaced as an application error after the retry
        budget, and callers must never mark coverage complete unless valid candles
        are actually returned.
        """
        try:
            datetime.strptime(from_date, "%Y-%m-%d %H:%M")
            datetime.strptime(to_date, "%Y-%m-%d %H:%M")
        except ValueError as e:
            raise TradingAppException(
                "InvalidDateFormat",
                f"Invalid date format: {str(e)}",
                400,
            )

        client = self.market_client.get_client()
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = client.getCandleData(
                    {
                        "exchange": exchange,
                        "symboltoken": symboltoken,
                        "interval": interval,
                        "fromdate": from_date,
                        "todate": to_date,
                    }
                )

                if response and response.get("status"):
                    return response

                message = (
                    response.get("message", "Unknown historical data error")
                    if response
                    else "Empty response from Angel One"
                )
                raise TradingAppException(
                    "HistoricalDataRequestFailed",
                    message,
                    502,
                )
            except TradingAppException as exc:
                last_error = exc
                # API-level failures may be rate limits/transient gateway errors.
                if attempt >= self.max_retries:
                    raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    app_logger.error(f"Historical data request failed: {str(exc)}")
                    raise TradingAppException(
                        "HistoricalDataRequestError",
                        str(exc),
                        502,
                    )

            delay = self.backoff_seconds * (2**attempt)
            if delay:
                self.sleep_fn(delay)

        # Defensive: the loop always returns or raises.
        raise TradingAppException(
            "HistoricalDataRequestError",
            str(last_error) if last_error else "Historical data request failed",
            502,
        )
