from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable


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


@runtime_checkable
class LiveMarketDataFeed(Protocol):
    """Broker-neutral lifecycle contract for live market-data adapters.

    Implementations own their transport and dispatch. Strategy consumers are
    injected as callbacks at construction; scanners do not depend on a broker
    SDK, token format, or WebSocket implementation.
    """

    def start(self) -> None: ...
    def subscribe(self, descriptors: Iterable[Any]) -> int: ...
    def unsubscribe(self, descriptors: Iterable[Any]) -> int: ...
    def stop(self) -> None: ...
    def snapshot(self) -> dict[str, Any]: ...
