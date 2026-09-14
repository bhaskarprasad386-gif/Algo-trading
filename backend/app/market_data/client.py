from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, Optional, TypeVar

from app.algo.auth import AngelOneAuth
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger

T = TypeVar("T")


class MarketDataClient:
    """Uses shared AngelOneAuth session for market data and broker calculations."""

    def __init__(
        self,
        auth: Optional[AngelOneAuth] = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
    ):
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if not math.isfinite(float(retry_backoff_seconds)) or retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be finite and non-negative")
        self.auth = auth or AngelOneAuth()
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def get_client(self):
        return self.auth.get_client()

    def _request_with_retry(self, operation: str, call: Callable[[], T]) -> T:
        """Retry transient broker/network exceptions with bounded exponential backoff."""
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                return call()
            except TradingAppException:
                raise
            except Exception as exc:
                if attempt >= self.max_retries:
                    raise
                delay = self.retry_backoff_seconds * (2**attempt)
                app_logger.warning(
                    f"{operation} transient failure; retry {attempt + 1}/{self.max_retries} "
                    f"after {delay:g}s: {exc}"
                )
                if delay:
                    time.sleep(delay)
        raise RuntimeError(f"{operation} request retry loop exhausted")

    def ltp(self, exchange: str, tradingsymbol: str, symboltoken: str) -> Dict[str, Any]:
        """Fetch latest traded price."""
        try:
            response = self._request_with_retry(
                f"LTP {tradingsymbol}",
                lambda: self.get_client().ltpData(exchange, tradingsymbol, symboltoken),
            )
            if response and response.get("status"):
                return response
            message = response.get("message", "Unknown LTP error") if response else "Empty response from Angel One"
            raise TradingAppException("LTPRequestFailed", message, 502)
        except TradingAppException:
            raise
        except Exception as e:
            app_logger.error(f"LTP request failed for {tradingsymbol}: {str(e)}")
            raise TradingAppException("LTPRequestError", "Market data provider request failed", 502) from e

    def quote(self, exchange: str, tradingsymbol: str, symboltoken: str) -> Dict[str, Any]:
        """Fetch Angel One FULL quote including volume/OI/depth when available."""
        try:
            response = self._request_with_retry(
                f"Quote {tradingsymbol}",
                lambda: self.get_client().getMarketData("FULL", {exchange.upper(): [str(symboltoken)]}),
            )
            if response and response.get("status"):
                return response
            message = response.get("message", "Unknown market-data error") if response else "Empty response from Angel One"
            raise TradingAppException("QuoteRequestFailed", message, 502)
        except TradingAppException:
            raise
        except Exception as e:
            app_logger.error(f"Quote request failed for {tradingsymbol}: {str(e)}")
            raise TradingAppException("QuoteRequestError", "Market data provider request failed", 502) from e

    def margin(self, positions: list[dict[str, Any]]) -> Dict[str, Any]:
        """Fetch Angel One's real-time margin requirement for a position basket."""
        if not positions:
            raise ValueError("positions must not be empty")
        try:
            response = self._request_with_retry(
                "Margin calculation",
                lambda: self.get_client().getMarginApi({"positions": positions}),
            )
            if response and response.get("status"):
                return response
            message = response.get("message", "Unknown margin calculation error") if response else "Empty response from Angel One"
            raise TradingAppException("MarginRequestFailed", message, 502)
        except TradingAppException:
            raise
        except Exception as e:
            app_logger.error(f"Margin calculation failed: {str(e)}")
            raise TradingAppException("MarginRequestError", "Market margin provider request failed", 502) from e

    def profile(self) -> Dict[str, Any]:
        """Fetch user profile (auth test)."""
        try:
            response = self._request_with_retry(
                "Profile request",
                lambda: self.get_client().getProfile(self.auth.get_jwt_token()),
            )
            if response and response.get("status"):
                return response
            message = response.get("message", "Profile fetch failed") if response else "Empty response"
            raise TradingAppException("ProfileError", message, 502)
        except TradingAppException:
            raise
        except Exception as e:
            app_logger.error(f"Profile request failed: {str(e)}")
            raise TradingAppException("ProfileError", "Market profile provider request failed", 502) from e
