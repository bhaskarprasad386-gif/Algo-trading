from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HistoricalRequest:
    symbol: str
    interval: str
    from_date: str
    to_date: str


class MarketDataProvider(Protocol):
    def historical(self, request: HistoricalRequest) -> list[dict]: ...


class AngelOneHistoricalProvider:
    """Adapter boundary for Angel One historical API; credentials stay outside code."""

    def __init__(self, client) -> None:
        self.client = client

    def historical(self, request: HistoricalRequest) -> list[dict]:
        response = self.client.historicalData(
            {
                "exchange": "NSE",
                "symboltoken": request.symbol,
                "interval": request.interval,
                "fromdate": request.from_date,
                "todate": request.to_date,
            }
        )
        return response.get("data") or []
