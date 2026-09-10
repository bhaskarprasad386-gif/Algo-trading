"""Angel One historical candle adapter for synchronized Cash-Future history."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from app.market_data.historical import HistoricalDataClient
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_historical_ingest import CashFutureHistoricalSource

IST = ZoneInfo("Asia/Kolkata")


class AngelOneCashFutureHistoricalSource(CashFutureHistoricalSource):
    """Build synchronized Cash-Future points from Angel One 1-minute candles.

    ``token_resolver`` supplies the source-backed NSE cash token and NFO futures
    token for a symbol/contract month. The adapter intentionally does not guess
    instrument tokens or contract symbols.
    """

    def __init__(
        self,
        historical_client: HistoricalDataClient | None = None,
        *,
        token_resolver: Callable[[str, str], tuple[str, str]] | None = None,
    ) -> None:
        if token_resolver is None:
            raise ValueError("token_resolver is required")
        self.historical_client = historical_client or HistoricalDataClient()
        self.token_resolver = token_resolver

    @staticmethod
    def _candle_map(response: dict) -> dict[datetime, float]:
        candles = response.get("data") or []
        result: dict[datetime, float] = {}
        for candle in candles:
            if len(candle) < 5:
                raise ValueError("Angel One candle record is incomplete")
            timestamp = datetime.fromisoformat(str(candle[0]).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=IST)
            timestamp = timestamp.astimezone(IST)
            result[timestamp] = float(candle[4])
        return result

    def fetch(
        self,
        *,
        symbol: str,
        contract_month: str,
        start: datetime,
        end: datetime,
    ) -> Iterable[CashFutureHistoryPoint]:
        cash_token, future_token = self.token_resolver(symbol, contract_month)
        from_date = start.astimezone(IST).strftime("%Y-%m-%d %H:%M")
        to_date = end.astimezone(IST).strftime("%Y-%m-%d %H:%M")
        cash_response = self.historical_client.get_candles(
            "NSE", cash_token, "ONE_MINUTE", from_date, to_date
        )
        future_response = self.historical_client.get_candles(
            "NFO", future_token, "ONE_MINUTE", from_date, to_date
        )
        cash = self._candle_map(cash_response)
        future = self._candle_map(future_response)

        for timestamp in sorted(cash.keys() & future.keys()):
            cash_price = cash[timestamp]
            future_price = future[timestamp]
            gap = future_price - cash_price
            gap_pct = (gap / cash_price * 100.0) if cash_price else 0.0
            yield CashFutureHistoryPoint(
                timestamp=timestamp,
                symbol=symbol.upper(),
                contract_month=contract_month,
                cash_price=cash_price,
                future_price=future_price,
                gap=gap,
                gap_pct=gap_pct,
                lot_size=0,
                margin_required=0.0,
            )


__all__ = ["AngelOneCashFutureHistoricalSource"]
