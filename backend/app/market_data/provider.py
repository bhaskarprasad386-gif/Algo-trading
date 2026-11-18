from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class HistoricalRequest:
    exchange: str
    symboltoken: str
    interval: str
    from_date: str
    to_date: str


class MarketDataProvider(Protocol):
    def historical(self, request: HistoricalRequest) -> list[Any]: ...


class AngelOneHistoricalProvider:
    """Adapter over the existing MarketDataClient historical API."""

    def __init__(self, market_client) -> None:
        self.market_client = market_client

    def historical(self, request: HistoricalRequest) -> list[Any]:
        response = self.market_client.get_client().getCandleData(
            {
                "exchange": request.exchange,
                "symboltoken": request.symboltoken,
                "interval": request.interval,
                "fromdate": request.from_date,
                "todate": request.to_date,
            }
        )
        return (response or {}).get("data") or []
