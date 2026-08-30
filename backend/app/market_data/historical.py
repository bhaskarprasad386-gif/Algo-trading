from datetime import datetime
from typing import Any, Dict

from app.market_data.client import MarketDataClient
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class HistoricalDataClient:
    """Wrapper for Angel One historical candle data."""

    def __init__(self, market_client: MarketDataClient | None = None):
        self.market_client = market_client or MarketDataClient()

    def get_candles(
        self,
        exchange: str,
        symboltoken: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> Dict[str, Any]:
        """Fetch historical candle data from Angel One."""

        try:
            datetime.fromisoformat(from_date)
            datetime.fromisoformat(to_date)

            client = self.market_client.get_client()

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
                response.get(
                    "message",
                    "Unknown historical data error",
                )
                if response
                else "Empty response from Angel One"
            )

            raise TradingAppException(
                "HistoricalDataRequestFailed",
                message,
                502,
            )

        except TradingAppException:
            raise

        except ValueError as e:
            raise TradingAppException(
                "InvalidDateFormat",
                f"Invalid date format: {str(e)}",
                400,
            )

        except Exception as e:
            app_logger.error(
                f"Historical data request failed: {str(e)}"
            )
            raise TradingAppException(
                "HistoricalDataRequestError",
                str(e),
                502,
            )
