"""m.Stock current-day Order Book and Trade Book history provider."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.brokers.history import BrokerHistoryUnavailable, validated_daily_history_window
from app.brokers.mstock import MStockAdapter, MStockHistoryUnavailable


_IST = ZoneInfo("Asia/Kolkata")


class MStockHistoryProvider:
    """Adapt m.Stock's current-day order/trade books to the common contract.

    The current-day endpoints are not treated as date-range history. Requests
    outside one complete current IST calendar day are rejected before API calls.
    """

    name = "mstock"

    def __init__(self, adapter: MStockAdapter) -> None:
        self._adapter = adapter

    def fetch_order_trade_snapshot(
        self,
        *,
        account_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> BrokerHistoryWindow:
        if window_start.tzinfo is None or window_start.utcoffset() is None:
            raise BrokerHistoryUnavailable("m.Stock history window_start must be timezone-aware")
        if window_end.tzinfo is None or window_end.utcoffset() is None:
            raise BrokerHistoryUnavailable("m.Stock history window_end must be timezone-aware")
        local_start = window_start.astimezone(_IST)
        local_end = window_end.astimezone(_IST)
        today_start = datetime.combine(datetime.now(_IST).date(), time.min, tzinfo=_IST)
        expected_end = today_start + timedelta(days=1)
        if local_start != today_start or local_end != expected_end:
            raise BrokerHistoryUnavailable(
                "m.Stock Order Book/Trade Book support only the complete current IST calendar day"
            )
        try:
            daily = self._adapter.fetch_today_broker_snapshot(account_id=account_id)
        except MStockHistoryUnavailable as exc:
            raise BrokerHistoryUnavailable(str(exc)) from exc
        return validated_daily_history_window(
            provider_name=self.name,
            account_id=account_id,
            window_start=window_start,
            window_end=window_end,
            daily_history=daily,
            provider_window="CURRENT_TRADING_DAY_ORDER_BOOK_AND_TRADE_BOOK",
            expected_trading_date=local_start.date().isoformat(),
        )
