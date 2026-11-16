from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyotp
from SmartApi import SmartConnect

from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerSnapshot,
    BrokerTradeSnapshot,
)


class BrokerHistoryUnavailable(RuntimeError):
    """Raised when broker history cannot be proven complete for its supported window."""


@dataclass(frozen=True)
class AngelOneDailyHistory:
    """Complete Angel One order/trade books for the current trading day only."""

    trading_date: str
    snapshot: BrokerSnapshot
    scope: str = "CURRENT_TRADING_DAY"


class AngelOneAdapter:
    """Angel One SmartAPI adapter.

    Credentials are accepted only at runtime. Session objects/tokens stay in
    process memory and are never returned by the API or persisted by this adapter.
    Live order routing remains disabled until the platform safety gate is enabled.
    """

    name = "angel_one"

    def __init__(self) -> None:
        self.smart_api: SmartConnect | None = None
        self.client_code: str | None = None
        self.account_id: str | None = None
        self.connected = False

    def connect(self, **credentials: Any) -> dict:
        required = ("api_key", "client_code", "password", "totp_secret")
        missing = [key for key in required if not credentials.get(key)]
        if missing:
            raise ValueError(f"Missing Angel One authentication fields: {', '.join(missing)}")

        api_key = str(credentials["api_key"]).strip()
        client_code = str(credentials["client_code"]).strip()
        password = str(credentials["password"]).strip()
        totp_secret = str(credentials["totp_secret"]).strip()

        smart_api = SmartConnect(api_key=api_key)
        totp = pyotp.TOTP(totp_secret).now()
        response = smart_api.generateSession(client_code, password, totp)
        if not isinstance(response, dict) or not response.get("status"):
            message = "Angel One authentication failed"
            if isinstance(response, dict):
                message = str(response.get("message") or response.get("errorcode") or message)
            raise ValueError(message)

        self.smart_api = smart_api
        self.client_code = client_code
        account_id = credentials.get("account_id")
        self.account_id = str(account_id).strip() if account_id else None
        self.connected = True
        return {"broker": self.name, "connected": True, "client_code": client_code}


    @staticmethod
    def _history_rows(response: Any, endpoint: str) -> list[dict[str, Any]]:
        """Accept only a successful SmartAPI response with an explicit row list."""
        if not isinstance(response, dict) or response.get("status") is not True:
            message = response.get("message") if isinstance(response, dict) else None
            raise BrokerHistoryUnavailable(
                f"Angel One {endpoint} did not return a successful response: {message or 'invalid response'}"
            )
        rows = response.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise BrokerHistoryUnavailable(
                f"Angel One {endpoint} response is not a complete row list"
            )
        return rows

    @staticmethod
    def _required_text(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        raise BrokerHistoryUnavailable(
            f"Angel One history row is missing required field: {'/'.join(keys)}"
        )

    @staticmethod
    def _nonnegative_int(row: dict[str, Any], *keys: str) -> int:
        value = None
        for key in keys:
            if row.get(key) not in (None, ""):
                value = row[key]
                break
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            raise BrokerHistoryUnavailable(
                f"Angel One history row has invalid numeric field: {'/'.join(keys)}"
            ) from None
        if parsed < 0:
            raise BrokerHistoryUnavailable(
                f"Angel One history row has negative field: {'/'.join(keys)}"
            )
        return parsed

    @staticmethod
    def _positive_int(row: dict[str, Any], *keys: str) -> int:
        value = None
        for key in keys:
            if row.get(key) not in (None, ""):
                value = row[key]
                break
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            raise BrokerHistoryUnavailable(
                f"Angel One history row has invalid numeric field: {'/'.join(keys)}"
            ) from None
        if parsed <= 0:
            raise BrokerHistoryUnavailable(
                f"Angel One history row has non-positive field: {'/'.join(keys)}"
            )
        return parsed

    def fetch_today_broker_snapshot(self, *, account_id: str) -> AngelOneDailyHistory:
        """Fetch read-only orderBook + tradeBook for the current trading day.

        SmartAPI's orderBook/tradeBook endpoints are day-scoped. This method
        deliberately does not claim multi-day history completeness and refuses
        to synthesize an empty book from failed, null, or malformed responses.
        Both endpoints must succeed before a complete BrokerSnapshot is returned.
        """
        if not self.connected or self.smart_api is None:
            raise BrokerHistoryUnavailable("Angel One session is not connected")
        if not self.account_id or account_id != self.account_id:
            raise BrokerHistoryUnavailable("Angel One account scope is not bound to this session")

        try:
            order_response = self.smart_api.orderBook()
            trade_response = self.smart_api.tradeBook()
        except Exception as exc:
            raise BrokerHistoryUnavailable(
                f"Angel One order/trade history request failed: {type(exc).__name__}"
            ) from exc

        order_rows = self._history_rows(order_response, "orderBook")
        trade_rows = self._history_rows(trade_response, "tradeBook")

        orders = tuple(
            BrokerOrderSnapshot(
                broker_order_id=self._required_text(row, "orderid", "order_id"),
                symbol=self._required_text(row, "tradingsymbol", "symbol"),
                side=self._required_text(row, "transactiontype", "transaction_type").upper(),
                quantity=self._positive_int(row, "quantity", "orderquantity"),
                filled_quantity=self._nonnegative_int(
                    row, "filledshares", "filled_quantity"
                ),
                raw_status=str(row.get("status") or row.get("orderstatus") or ""),
            )
            for row in order_rows
        )
        trades = tuple(
            BrokerTradeSnapshot(
                broker_trade_id=self._required_text(row, "tradeid", "trade_id"),
                broker_order_id=self._required_text(row, "orderid", "order_id"),
                quantity=self._positive_int(row, "fillshares", "quantity", "filled_quantity"),
            )
            for row in trade_rows
        )

        from datetime import datetime
        from zoneinfo import ZoneInfo

        trading_date = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        return AngelOneDailyHistory(
            trading_date=trading_date,
            snapshot=BrokerSnapshot(
                account_id=account_id,
                broker=self.name,
                orders=orders,
                trades=trades,
                complete=True,
            ),
        )

    def place_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Live order routing is disabled until broker safety approval")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Live order routing is disabled until broker safety approval")

    def disconnect(self) -> None:
        if self.smart_api is not None:
            try:
                self.smart_api.terminateSession(self.client_code)
            except Exception:
                pass
        self.smart_api = None
        self.client_code = None
        self.account_id = None
        self.connected = False
