"""Shoonya current-day order/trade books as a read-only history provider."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.brokers.history import BrokerHistoryUnavailable, BrokerHistoryWindow
from app.brokers.shoonya import ShoonyaAdapter, ShoonyaHistoryUnavailable


_IST = ZoneInfo("Asia/Kolkata")


class ShoonyaHistoryProvider:
    """Adapt Shoonya's current-day Order Book and Trade Book to the common contract.

    Shoonya's books are treated as current trading-day evidence only. This
    provider does not infer multi-day history from the current-day endpoints.
    """

    name = "shoonya"

    def __init__(self, adapter: ShoonyaAdapter) -> None:
        self._adapter = adapter

    def fetch_order_trade_snapshot(
        self,
        *,
        account_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> BrokerHistoryWindow:
        if window_start.tzinfo is None or window_start.utcoffset() is None:
            raise BrokerHistoryUnavailable("Shoonya history window_start must be timezone-aware")
        if window_end.tzinfo is None or window_end.utcoffset() is None:
            raise BrokerHistoryUnavailable("Shoonya history window_end must be timezone-aware")
        local_start = window_start.astimezone(_IST)
        local_end = window_end.astimezone(_IST)
        today_start = datetime.combine(datetime.now(_IST).date(), time.min, tzinfo=_IST)
        expected_end = today_start + timedelta(days=1)
        if local_start != today_start or local_end != expected_end:
            raise BrokerHistoryUnavailable(
                "Shoonya Order Book/Trade Book support only the complete current IST calendar day"
            )
        try:
            daily = self._adapter.fetch_today_broker_snapshot(account_id=account_id)
        except ShoonyaHistoryUnavailable as exc:
            raise BrokerHistoryUnavailable(str(exc)) from exc
        return BrokerHistoryWindow(
            snapshot=daily.snapshot,
            window_start=window_start,
            window_end=window_end,
            pages_fetched=1,
            has_more=False,
            provider_window="CURRENT_TRADING_DAY_ORDER_BOOK_AND_TRADE_BOOK",
        )
