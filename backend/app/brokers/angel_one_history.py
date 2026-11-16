"""Angel One implementation of the provider-neutral read-only history contract."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.brokers.angel_one import AngelOneAdapter, BrokerHistoryUnavailable
from app.brokers.history import validated_daily_history_window


_IST = ZoneInfo("Asia/Kolkata")


class AngelOneHistoryProvider:
    """Expose Angel One's current-day books without overstating history coverage."""

    name = "angel_one"

    def __init__(self, adapter: AngelOneAdapter) -> None:
        self._adapter = adapter

    def fetch_order_trade_snapshot(
        self,
        *,
        account_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> BrokerHistoryWindow:
        if window_start.tzinfo is None or window_start.utcoffset() is None:
            raise BrokerHistoryUnavailable("history window_start must be timezone-aware")
        if window_end.tzinfo is None or window_end.utcoffset() is None:
            raise BrokerHistoryUnavailable("history window_end must be timezone-aware")

        local_start = window_start.astimezone(_IST)
        local_end = window_end.astimezone(_IST)
        if local_end <= local_start:
            raise BrokerHistoryUnavailable("history window_end must be later than window_start")

        # SmartAPI orderBook/tradeBook expose only today's books. Accept only
        # the complete IST calendar-day window for the current local date.
        today = datetime.now(_IST).date()
        expected_start = datetime.combine(today, datetime.min.time(), tzinfo=_IST)
        expected_end = expected_start + timedelta(days=1)
        if local_start != expected_start or local_end != expected_end:
            raise BrokerHistoryUnavailable(
                "Angel One history supports only the complete current IST trading-day window"
            )

        daily = self._adapter.fetch_today_broker_snapshot(account_id=account_id)
        return validated_daily_history_window(
            provider_name=self.name,
            account_id=account_id,
            window_start=window_start,
            window_end=window_end,
            daily_history=daily,
            provider_window="CURRENT_TRADING_DAY",
        )
