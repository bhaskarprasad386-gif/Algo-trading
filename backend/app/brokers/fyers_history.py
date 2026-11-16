"""FYERS date-range reports implementation of the read-only history contract."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from app.brokers.fyers import FyersAdapter, FyersHistoryUnavailable
from app.brokers.history import BrokerHistoryUnavailable, BrokerHistoryWindow
from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerSnapshot,
    BrokerTradeSnapshot,
)


_IST = ZoneInfo("Asia/Kolkata")
_PAGE_SIZE = 100
_MAX_PAGES_PER_REPORT = 1000


class FyersHistoryProvider:
    """Fetch complete closed-day order/trade reports using FYERS report pagination.

    FYERS order-history reports exclude pending orders. This provider therefore
    accepts only fully closed IST calendar days strictly before today. Current
    day pending/open orders must be obtained from the separate order book path.
    """

    name = "fyers"

    def __init__(self, adapter: FyersAdapter) -> None:
        self._adapter = adapter

    @staticmethod
    def _validate_response(response: Any, endpoint: str) -> list[dict[str, Any]]:
        if not isinstance(response, dict) or response.get("s") != "ok":
            raise BrokerHistoryUnavailable(f"FYERS {endpoint} report did not succeed")
        if response.get("code") not in (None, 200):
            raise BrokerHistoryUnavailable(f"FYERS {endpoint} report returned an invalid status code")
        rows = response.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise BrokerHistoryUnavailable(f"FYERS {endpoint} report is not a complete row list")
        return rows

    def _fetch_pages(self, method: Any, *, data: dict[str, str], endpoint: str) -> tuple[list[dict[str, Any]], int]:
        all_rows: list[dict[str, Any]] = []
        for page_no in range(1, _MAX_PAGES_PER_REPORT + 1):
            request = {**data, "page_size": str(_PAGE_SIZE), "page_no": str(page_no)}
            try:
                response = method(data=request)
            except Exception as exc:
                raise BrokerHistoryUnavailable(
                    f"FYERS {endpoint} page {page_no} request failed: {type(exc).__name__}"
                ) from exc
            rows = self._validate_response(response, endpoint)
            all_rows.extend(rows)
            if len(rows) < _PAGE_SIZE:
                return all_rows, page_no
        raise BrokerHistoryUnavailable(
            f"FYERS {endpoint} exceeded the safe {_MAX_PAGES_PER_REPORT}-page history limit"
        )

    @staticmethod
    def _order(row: dict[str, Any]) -> BrokerOrderSnapshot:
        order_id = FyersAdapter._text(row, "id_fyers", "id")
        symbol = FyersAdapter._text(row, "symbol")
        side_value = row.get("transaction_type")
        if side_value is None:
            side_value = row.get("side")
        if isinstance(side_value, str) and side_value.upper() in {"BUY", "SELL"}:
            side = side_value.upper()
        else:
            try:
                numeric_side = int(str(side_value))
            except (TypeError, ValueError):
                raise BrokerHistoryUnavailable("FYERS order history row has invalid transaction side") from None
            side = {1: "BUY", -1: "SELL"}.get(numeric_side)
            if side is None:
                raise BrokerHistoryUnavailable("FYERS order history row has invalid transaction side")
        quantity = FyersAdapter._integer(row, positive=True, keys=("qty", "quantity"))
        filled = FyersAdapter._integer(
            row, positive=False, keys=("tradedQty", "tradedqty", "filledQty", "filled_quantity")
        )
        if filled > quantity:
            raise BrokerHistoryUnavailable("FYERS order history fill quantity exceeds order quantity")
        return BrokerOrderSnapshot(
            broker_order_id=order_id, symbol=symbol, side=side,
            quantity=quantity, filled_quantity=filled,
        )

    @staticmethod
    def _trade(row: dict[str, Any]) -> BrokerTradeSnapshot:
        return BrokerTradeSnapshot(
            broker_trade_id=FyersAdapter._text(row, "tradeNumber", "trade_number"),
            broker_order_id=FyersAdapter._text(row, "orderNumber", "order_number"),
            quantity=FyersAdapter._integer(
                row, positive=True, keys=("traded_qty", "tradedQty", "quantity")
            ),
        )

    def fetch_order_trade_snapshot(
        self,
        *,
        account_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> BrokerHistoryWindow:
        if not self._adapter.connected or not self._adapter.account_id:
            raise BrokerHistoryUnavailable("FYERS session is not connected")
        if account_id != self._adapter.account_id:
            raise BrokerHistoryUnavailable("FYERS account scope does not match this session")
        if window_start.tzinfo is None or window_start.utcoffset() is None:
            raise BrokerHistoryUnavailable("history window_start must be timezone-aware")
        if window_end.tzinfo is None or window_end.utcoffset() is None:
            raise BrokerHistoryUnavailable("history window_end must be timezone-aware")

        local_start = window_start.astimezone(_IST)
        local_end = window_end.astimezone(_IST)
        if local_end <= local_start:
            raise BrokerHistoryUnavailable("history window_end must be later than window_start")
        if local_start.time() != time.min or local_end.time() != time.min:
            raise BrokerHistoryUnavailable("FYERS date reports require complete IST calendar-day windows")
        today_start = datetime.combine(datetime.now(_IST).date(), time.min, tzinfo=_IST)
        if local_end > today_start:
            raise BrokerHistoryUnavailable(
                "FYERS date-range provider covers closed IST days only; current-day pending orders require orderbook"
            )

        from_date = local_start.date().isoformat()
        to_date = (local_end.date() - timedelta(days=1)).isoformat()
        if not from_date or not to_date:
            raise BrokerHistoryUnavailable("FYERS history date range is invalid")
        request = {"from_date": from_date, "to_date": to_date}

        try:
            order_rows, order_pages = self._fetch_pages(
                self._adapter.client.orderhistory, data=request, endpoint="order-history",
            )
            trade_rows, trade_pages = self._fetch_pages(
                self._adapter.client.tradehistory, data=request, endpoint="trade-history",
            )
        except AttributeError as exc:
            raise BrokerHistoryUnavailable(
                "FYERS client does not expose orderhistory and tradehistory report methods"
            ) from exc

        orders = tuple(self._order(row) for row in order_rows)
        trades = tuple(self._trade(row) for row in trade_rows)
        order_ids = [item.broker_order_id for item in orders]
        trade_ids = [item.broker_trade_id for item in trades]
        if len(order_ids) != len(set(order_ids)):
            raise BrokerHistoryUnavailable("FYERS order-history report contains duplicate order IDs")
        if len(trade_ids) != len(set(trade_ids)):
            raise BrokerHistoryUnavailable("FYERS trade-history report contains duplicate trade IDs")

        snapshot = BrokerSnapshot(
            account_id=account_id,
            broker=self.name,
            orders=orders,
            trades=trades,
            complete=True,
        )
        return BrokerHistoryWindow(
            snapshot=snapshot,
            window_start=window_start,
            window_end=window_end,
            pages_fetched=order_pages + trade_pages,
            has_more=False,
            provider_window="FYERS_CLOSED_DAY_DATE_RANGE_REPORTS",
        )
