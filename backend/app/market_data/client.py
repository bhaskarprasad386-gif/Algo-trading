from typing import Any, Dict, Optional

from app.algo.auth import AngelOneAuth
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class MarketDataClient:
    """Wrapper around the authenticated Angel One SmartAPI client."""

    def __init__(self, auth: Optional[AngelOneAuth] = None):
        self.auth = auth or AngelOneAuth()
        self.smart_api = None

    def connect(self):
        """Get or create an authenticated SmartAPI client."""
        try:
            self.smart_api = self.auth.get_client()
            app_logger.info("Angel One market data client connected successfully")
            return self.smart_api

        except TradingAppException:
            raise

        except Exception as e:
            app_logger.error(
                f"Failed to connect market data client: {str(e)}"
            )
            raise TradingAppException(
                "MarketDataConnectionError",
                f"Could not connect to Angel One market data client: {str(e)}",
                500,
            )

    def get_client(self):
        """Return the authenticated SmartAPI client."""
        if self.smart_api is None:
            return self.connect()

        return self.smart_api

    def ltp(self, exchange: str, tradingsymbol: str, symboltoken: str) -> Dict[str, Any]:
        """Fetch the latest traded price for an instrument."""
        try:
            client = self.get_client()

            response = client.ltpData(
                exchange,
                tradingsymbol,
                symboltoken,
            )

            if response and response.get("status"):
                return response

            message = (
                response.get("message", "Unknown LTP error")
                if response
                else "Empty response from Angel One"
            )

            raise TradingAppException(
                "LTPRequestFailed",
                message,
                502,
            )

        except TradingAppException:
            raise

        except Exception as e:
            app_logger.error(
                f"LTP request failed for {tradingsymbol}: {str(e)}"
            )
            raise TradingAppException(
                "LTPRequestError",
                str(e),
                502,
            )
