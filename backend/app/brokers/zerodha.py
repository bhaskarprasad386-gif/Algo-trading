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


class ZerodhaHistoryUnavailable(RuntimeError):
    """Raised when Zerodha history cannot be proven complete for the current day."""


@dataclass(frozen=True)
class ZerodhaDailyHistory:
    trading_date: str
    snapshot: BrokerSnapshot
    scope: str = "CURRENT_TRADING_DAY"


class ZerodhaAdapter:
    """Read-only Kite Connect history adapter.

    A configured Kite Connect client is injected by the authenticated owner
    session layer. The adapter validates the profile identity before accepting
    an access token and requires both today's order and trade books. It does not
    implement interactive login, token renewal, or order mutation.
    """

    name = "zerodha"

    def __init__(self, client: Any) -> None:
        if client is None:
            raise ValueError("A configured Kite Connect client is required")
        self.client = client
        self.account_id: str | None = None
        self.connected = False

    def connect(self, **credentials: Any) -> dict[str, Any]:
        access_token = str(credentials.get("access_token") or "").strip()
        account_id = str(credentials.get("account_id") or "").strip()
        if not access_token or not account_id:
            raise ValueError("Missing Zerodha authentication fields: access_token, account_id")
        try:
            self.client.set_access_token(access_token)
            profile = self.client.profile()
        except Exception as exc:
            self.disconnect()
            raise ZerodhaHistoryUnavailable(
                f"Zerodha authentication failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(profile, dict):
            self.disconnect()
            raise ZerodhaHistoryUnavailable("Zerodha profile response is invalid")
        authenticated_id = str(profile.get("user_id") or "").strip()
        if not authenticated_id or authenticated_id != account_id:
            self.disconnect()
            raise ZerodhaHistoryUnavailable("Zerodha authenticated account does not match requested account")
        self.account_id = authenticated_id
        self.connected = True
        return {"broker": self.name, "connected": True, "account_id": authenticated_id}

    @staticmethod
    def _rows(value: Any, endpoint: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ZerodhaHistoryUnavailable(
                f"Zerodha {endpoint} response is not a complete row list"
            )
        return value

    @staticmethod
    def _text(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        raise ZerodhaHistoryUnavailable(
            f"Zerodha history row is missing required field: {'/'.join(keys)}"
        )

    @staticmethod
    def _integer(row: dict[str, Any], *, positive: bool, keys: tuple[str, ...]) -> int:
        value = next((row[key] for key in keys if row.get(key) not in (None, "")), None)
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            raise ZerodhaHistoryUnavailable(
                f"Zerodha history row has invalid numeric field: {'/'.join(keys)}"
            ) from None
        if parsed < (1 if positive else 0):
            raise ZerodhaHistoryUnavailable(
                f"Zerodha history row has invalid numeric field: {'/'.join(keys)}"
            )
        return parsed

    def fetch_today_broker_snapshot(self, *, account_id: str) -> ZerodhaDailyHistory:
        if not self.connected or not self.account_id:
            raise ZerodhaHistoryUnavailable("Zerodha session is not connected")
        if account_id != self.account_id:
            raise ZerodhaHistoryUnavailable("Zerodha account scope does not match this session")
        try:
            order_rows = self._rows(self.client.orders(), "orders")
            trade_rows = self._rows(self.client.trades(), "trades")
        except ZerodhaHistoryUnavailable:
            raise
        except Exception as exc:
            raise ZerodhaHistoryUnavailable(
                f"Zerodha order/trade history request failed: {type(exc).__name__}"
            ) from exc

        orders = tuple(
            BrokerOrderSnapshot(
                broker_order_id=self._text(row, "order_id"),
                symbol=self._text(row, "tradingsymbol"),
                side=self._text(row, "transaction_type").upper(),
                quantity=self._integer(row, positive=True, keys=("quantity",)),
                filled_quantity=self._integer(
                    row, positive=False, keys=("filled_quantity",)
                ),
                raw_status=str(row.get("status") or ""),
            )
            for row in order_rows
        )
        trades = tuple(
            BrokerTradeSnapshot(
                broker_trade_id=self._text(row, "trade_id"),
                broker_order_id=self._text(row, "order_id"),
                quantity=self._integer(row, positive=True, keys=("quantity",)),
            )
            for row in trade_rows
        )
        return ZerodhaDailyHistory(
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
        raise NotImplementedError("Zerodha live order routing is disabled pending Batch 0 safety approval")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Zerodha live order routing is disabled pending Batch 0 safety approval")

    def disconnect(self) -> None:
        try:
            logout = getattr(self.client, "logout", None)
            if callable(logout):
                logout()
            else:
                self.client.set_access_token("")
        except Exception:
            # Local adapter state is always cleared, even if remote logout fails.
            try:
                self.client.set_access_token("")
            except Exception:
                pass
        self.account_id = None
        self.connected = False
