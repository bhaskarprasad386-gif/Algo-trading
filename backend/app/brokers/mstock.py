from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerSnapshot,
    BrokerTradeSnapshot,
)


class MStockHistoryUnavailable(RuntimeError):
    """Raised when m.Stock history cannot be proven complete for its supported window."""


@dataclass(frozen=True)
class MStockDailyHistory:
    trading_date: str
    snapshot: BrokerSnapshot
    scope: str = "CURRENT_TRADING_DAY"


class MStockAdapter:
    """Read-only m.Stock Type A adapter.

    Authentication is supplied as an API key and already-issued access token.
    This adapter deliberately does not implement OTP/TOTP login or any order
    mutation API. Type A access tokens are short-lived and must be renewed by
    the authenticated owner flow outside this adapter.
    """

    name = "mstock"
    base_url = "https://api.mstock.trade/openapi/typea"

    def __init__(self, http: Any | None = None) -> None:
        self.http = http or requests.Session()
        self.api_key: str | None = None
        self.access_token: str | None = None
        self.account_id: str | None = None
        self.connected = False

    def connect(self, **credentials: Any) -> dict[str, Any]:
        required = ("api_key", "access_token", "account_id")
        missing = [key for key in required if not credentials.get(key)]
        if missing:
            raise ValueError(f"Missing m.Stock authentication fields: {', '.join(missing)}")

        api_key = str(credentials["api_key"]).strip()
        access_token = str(credentials["access_token"]).strip()
        account_id = str(credentials["account_id"]).strip()
        if not api_key or not access_token or not account_id:
            raise ValueError("m.Stock API key, access token and account ID must be non-empty")

        self.api_key = api_key
        self.access_token = access_token
        self.account_id = account_id
        self.connected = True
        return {"broker": self.name, "connected": True, "account_id": account_id}

    def _headers(self) -> dict[str, str]:
        if not self.connected or not self.api_key or not self.access_token:
            raise MStockHistoryUnavailable("m.Stock session is not connected")
        return {
            "X-Mirae-Version": "1",
            "Authorization": f"token {self.api_key}:{self.access_token}",
        }

    def _get_rows(self, endpoint: str) -> list[dict[str, Any]]:
        try:
            response = self.http.get(
                f"{self.base_url}/{endpoint}",
                headers=self._headers(),
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except MStockHistoryUnavailable:
            raise
        except Exception as exc:
            raise MStockHistoryUnavailable(
                f"m.Stock {endpoint} request failed: {type(exc).__name__}"
            ) from exc

        if not isinstance(payload, dict) or str(payload.get("status", "")).lower() != "success":
            raise MStockHistoryUnavailable(f"m.Stock {endpoint} did not return success")
        rows = payload.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise MStockHistoryUnavailable(f"m.Stock {endpoint} response is not a complete row list")
        return rows

    @staticmethod
    def _required_text(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        raise MStockHistoryUnavailable(
            f"m.Stock history row is missing required field: {'/'.join(keys)}"
        )

    @staticmethod
    def _integer(row: dict[str, Any], *, positive: bool, keys: tuple[str, ...]) -> int:
        value = next((row[key] for key in keys if row.get(key) not in (None, "")), None)
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            raise MStockHistoryUnavailable(
                f"m.Stock history row has invalid numeric field: {'/'.join(keys)}"
            ) from None
        if parsed < (1 if positive else 0):
            raise MStockHistoryUnavailable(
                f"m.Stock history row has invalid numeric field: {'/'.join(keys)}"
            )
        return parsed

    def fetch_today_broker_snapshot(self, *, account_id: str) -> MStockDailyHistory:
        if not self.connected or not self.account_id:
            raise MStockHistoryUnavailable("m.Stock session is not connected")
        if account_id != self.account_id:
            raise MStockHistoryUnavailable("m.Stock account scope does not match this session")

        # Both books are required. A successful empty list is valid evidence;
        # missing/null data or either endpoint failure is never treated as empty.
        order_rows = self._get_rows("orders")
        trade_rows = self._get_rows("tradebook")

        orders = tuple(
            BrokerOrderSnapshot(
                broker_order_id=self._required_text(row, "order_id"),
                symbol=self._required_text(row, "tradingsymbol"),
                side=self._required_text(row, "transaction_type").upper(),
                quantity=self._integer(row, positive=True, keys=("quantity",)),
                filled_quantity=self._integer(
                    row, positive=False, keys=("filled_quantity",)
                ),
            )
            for row in order_rows
        )
        trades = tuple(
            BrokerTradeSnapshot(
                broker_trade_id=self._required_text(row, "TRADE_NUMBER", "trade_id"),
                broker_order_id=self._required_text(row, "ORDER_NUMBER", "order_id"),
                quantity=self._integer(row, positive=True, keys=("QUANTITY", "quantity")),
            )
            for row in trade_rows
        )

        return MStockDailyHistory(
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
        raise NotImplementedError("m.Stock live order routing is disabled pending Batch 0 safety approval")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("m.Stock live order routing is disabled pending Batch 0 safety approval")

    def disconnect(self) -> None:
        self.api_key = None
        self.access_token = None
        self.account_id = None
        self.connected = False
