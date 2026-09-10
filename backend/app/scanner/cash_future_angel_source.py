"""Angel One historical candle adapter for synchronized Cash-Future history."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from app.market_data.historical import HistoricalDataClient
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_historical_ingest import CashFutureHistoricalSource

IST = ZoneInfo("Asia/Kolkata")


class AngelOneCashFutureHistoricalSource(CashFutureHistoricalSource):
    """Build synchronized Cash-Future points from bounded Angel One requests.

    ``token_resolver`` supplies source-backed NSE cash and NFO futures tokens.
    ``metadata_resolver`` supplies the contract lot size and historical margin
    value used by the backtest record; this adapter never invents either value.
    Requests are split into at most one calendar day, so a long history is never
    materialized in one in-memory response.
    """

    def __init__(
        self,
        historical_client: HistoricalDataClient | None = None,
        *,
        token_resolver: Callable[[str, str], tuple[str, str]] | None = None,
        metadata_resolver: Callable[[str, str], tuple[int, float]] | None = None,
    ) -> None:
        if token_resolver is None:
            raise ValueError("token_resolver is required")
        if metadata_resolver is None:
            raise ValueError("metadata_resolver is required")
        self.historical_client = historical_client or HistoricalDataClient()
        self.token_resolver = token_resolver
        self.metadata_resolver = metadata_resolver

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
            result[timestamp.astimezone(IST)] = float(candle[4])
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
        lot_size, margin_required = self.metadata_resolver(symbol, contract_month)
        if lot_size <= 0 or margin_required < 0:
            raise ValueError("metadata_resolver returned invalid contract metadata")

        cursor = start.astimezone(IST)
        final = end.astimezone(IST)
        while cursor <= final:
            session_end = min(cursor + timedelta(days=1) - timedelta(minutes=1), final)
            from_date = cursor.strftime("%Y-%m-%d %H:%M")
            to_date = session_end.strftime("%Y-%m-%d %H:%M")
            cash = self._candle_map(
                self.historical_client.get_candles("NSE", cash_token, "ONE_MINUTE", from_date, to_date)
            )
            future = self._candle_map(
                self.historical_client.get_candles("NFO", future_token, "ONE_MINUTE", from_date, to_date)
            )
            for timestamp in sorted(cash.keys() & future.keys()):
                if timestamp < start.astimezone(IST) or timestamp > final:
                    continue
                cash_price = cash[timestamp]
                future_price = future[timestamp]
                gap = future_price - cash_price
                yield CashFutureHistoryPoint(
                    timestamp=timestamp,
                    symbol=symbol.upper(),
                    contract_month=contract_month,
                    cash_price=cash_price,
                    future_price=future_price,
                    gap=gap,
                    gap_pct=(gap / cash_price * 100.0) if cash_price else 0.0,
                    lot_size=lot_size,
                    margin_required=margin_required,
                )
            cursor = session_end + timedelta(minutes=1)


__all__ = ["AngelOneCashFutureHistoricalSource"]
