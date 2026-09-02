from typing import Any, Dict, Optional

from app.algo.auth import AngelOneAuth
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class MarketDataClient:
    """Uses shared AngelOneAuth session for market data."""

    def __init__(self, auth: Optional[AngelOneAuth] = None):
        self.auth = auth or AngelOneAuth()

    def get_client(self):
        return self.auth.get_client()

    def ltp(self, exchange: str, tradingsymbol: str, symboltoken: str) -> Dict[str, Any]:
        """Fetch latest traded price."""
        try:
            client = self.get_client()
            response = client.ltpData(exchange, tradingsymbol, symboltoken)

            if response and response.get("status"):
                return response

            message = (
                response.get("message", "Unknown LTP error")
                if response
                else "Empty response from Angel One"
            )
            raise TradingAppException("LTPRequestFailed", message, 502)

        except TradingAppException:
            raise
        except Exception as e:
            app_logger.error(f"LTP request failed for {tradingsymbol}: {str(e)}")
            raise TradingAppException("LTPRequestError", str(e), 502)

    def quote(self, exchange: str, tradingsymbol: str, symboltoken: str) -> Dict[str, Any]:
        """Fetch Angel One FULL quote including volume/OI/depth when available."""
        try:
            client = self.get_client()
            response = client.getMarketData(
                "FULL",
                {exchange.upper(): [str(symboltoken)]},
            )
            if response and response.get("status"):
                return response
            message = (
                response.get("message", "Unknown market-data error")
                if response
                else "Empty response from Angel One"
            )
            raise TradingAppException("QuoteRequestFailed", message, 502)
        except TradingAppException:
            raise
        except Exception as e:
            app_logger.error(f"Quote request failed for {tradingsymbol}: {str(e)}")
            raise TradingAppException("QuoteRequestError", str(e), 502)

    def profile(self) -> Dict[str, Any]:
        """Fetch user profile (auth test)."""
        try:
            client = self.get_client()
            response = client.getProfile(self.auth.get_jwt_token())

            if response and response.get("status"):
                return response

            message = (
                response.get("message", "Profile fetch failed")
                if response
                else "Empty response"
            )
            raise TradingAppException("ProfileError", message, 502)

        except TradingAppException:
            raise
        except Exception as e:
            app_logger.error(f"Profile request failed: {str(e)}")
            raise TradingAppException("ProfileError", str(e), 502)
