from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerSnapshot,
    BrokerTradeSnapshot,
)


class FyersHistoryUnavailable(RuntimeError):
    """Raised when FYERS history cannot be proven complete for the current day."""


@dataclass(frozen=True)
class FyersDailyHistory:
    trading_date: str
    snapshot: BrokerSnapshot
    scope: str = "CURRENT_TRADING_DAY"


class FyersAdapter:
    """Read-only FYERS API v3 order/trade book adapter.

    A configured FYERS client is injected by the authenticated owner session
    layer. Profile fy_id is checked against the requested single account before
    fetching today's orderBook and tradeBook. No mutation API is exposed.
    """

    name = "fyers"

    def __init__(self, client: Any) -> None:
        if client is None:
            raise ValueError("A configured FYERS API client is required")
        self.client = client
        self.account_id: str | None = None
        self.connected = False

    def connect(self, **credentials: Any) -> dict[str, Any]:
        expected_account_id = str(credentials.get("account_id") or "").strip()
        if not expected_account_id:
            raise ValueError("Missing FYERS identity field: account_id")
        try:
            response = self.client.get_profile()
        except Exception as exc:
            self.disconnect()
            raise FyersHistoryUnavailable(
                f"FYERS profile request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(response, dict) or response.get("s") != "ok" or response.get("code") != 200:
            self.disconnect()
            raise FyersHistoryUnavailable("FYERS profile response is unsuccessful or invalid")
        profile = response.get("data")
        if not isinstance(profile, dict):
            self.disconnect()
            raise FyersHistoryUnavailable("FYERS profile response is missing account data")
        actual_account_id = str(profile.get("fy_id") or "").strip()
        if not actual_account_id or actual_account_id != expected_account_id:
            self.disconnect()
            raise FyersHistoryUnavailable(
                "FYERS authenticated account does not match requested account"
            )
        self.account_id = actual_account_id
        self.connected = True
        return {"broker": self.name, "connected": True, "account_id": actual_account_id}

    @staticmethod
    def _rows(response: Any, key: str, endpoint: str) -> list[dict[str, Any]]:
        if (
            not isinstance(response, dict)
            or response.get("s") != "ok"
            or response.get("code") != 200
        ):
            raise FyersHistoryUnavailable(
                f"FYERS {endpoint} did not return a successful response"
            )
        rows = response.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise FyersHistoryUnavailable(
                f"FYERS {endpoint} response is not a complete row list"
            )
        return rows

    @staticmethod
    def _text(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        raise FyersHistoryUnavailable(
            f"FYERS history row is missing required field: {'/'.join(keys)}"
        )

    @staticmethod
    def _integer(
        row: dict[str, Any], *, positive: bool, keys: tuple[str, ...]
    ) -> int:
        value = next((row[key] for key in keys if row.get(key) not in (None, "")), None)
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            raise FyersHistoryUnavailable(
                f"FYERS history row has invalid numeric field: {'/'.join(keys)}"
            ) from None
        if parsed < (1 if positive else 0):
            raise FyersHistoryUnavailable(
                f"FYERS history row has invalid numeric field: {'/'.join(keys)}"
            )
        return parsed

    @staticmethod
    def _side(row: dict[str, Any]) -> str:
        value = FyersAdapter._integer(row, positive=False, keys=("side",))
        mapped = {1: "BUY", -1: "SELL"}.get(value)
        if mapped is None:
            raise FyersHistoryUnavailable("FYERS history row has an invalid transaction side")
        return mapped

    def fetch_today_broker_snapshot(self, *, account_id: str) -> FyersDailyHistory:
        if not self.connected or not self.account_id:
            raise FyersHistoryUnavailable("FYERS session is not connected")
        if account_id != self.account_id:
            raise FyersHistoryUnavailable("FYERS account scope does not match this session")
        try:
            order_rows = self._rows(self.client.orderbook(), "orderBook", "order book")
            trade_rows = self._rows(self.client.tradebook(), "tradeBook", "trade book")
        except FyersHistoryUnavailable:
            raise
        except Exception as exc:
            raise FyersHistoryUnavailable(
                f"FYERS order/trade history request failed: {type(exc).__name__}"
            ) from exc

        orders = tuple(
            BrokerOrderSnapshot(
                broker_order_id=self._text(row, "id"),
                symbol=self._text(row, "symbol"),
                side=self._side(row),
                quantity=self._integer(row, positive=True, keys=("qty",)),
                filled_quantity=self._integer(
                    row, positive=False, keys=("filledQty",)
                ),
                raw_status=str(row.get("status") or ""),
            )
            for row in order_rows
        )
        trades = tuple(
            BrokerTradeSnapshot(
                broker_trade_id=self._text(row, "tradeNumber"),
                broker_order_id=self._text(row, "orderNumber"),
                quantity=self._integer(row, positive=True, keys=("tradedQty",)),
            )
            for row in trade_rows
        )
        return FyersDailyHistory(
            trading_date=datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
            snapshot=BrokerSnapshot(
                account_id=account_id,
                broker=self.name,
                orders=orders,
                trades=trades,
                complete=True,
            ),
        )

    def place_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("FYERS live order routing is disabled pending Batch 0 safety approval")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("FYERS live order routing is disabled pending Batch 0 safety approval")

    def disconnect(self) -> None:
        self.account_id = None
        self.connected = False
        disconnect = getattr(self.client, "disconnect", None)
        if callable(disconnect):
            disconnect()
