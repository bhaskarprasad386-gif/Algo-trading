from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerSnapshot,
    BrokerTradeSnapshot,
    parse_positive_trade_price,
    parse_trade_timestamp_ns,
)


class ShoonyaHistoryUnavailable(RuntimeError):
    """Raised when Shoonya history cannot be proven complete for the current day."""


@dataclass(frozen=True)
class ShoonyaDailyHistory:
    trading_date: str
    snapshot: BrokerSnapshot
    scope: str = "CURRENT_TRADING_DAY"


class ShoonyaAdapter:
    """Read-only Noren/Shoonya order and trade book adapter.

    The authenticated Shoonya client is injected by its owner session layer.
    This adapter validates both UID and account ID against get_user_details()
    before reading today's books. It deliberately has no order mutation path.
    """

    name = "shoonya"

    def __init__(self, client: Any) -> None:
        if client is None:
            raise ValueError("An authenticated Shoonya client is required")
        self.client = client
        self.account_id: str | None = None
        self.uid: str | None = None
        self.connected = False

    def connect(self, **credentials: Any) -> dict[str, Any]:
        expected_uid = str(credentials.get("uid") or "").strip()
        expected_account_id = str(credentials.get("account_id") or "").strip()
        if not expected_uid or not expected_account_id:
            raise ValueError("Missing Shoonya identity fields: uid, account_id")
        try:
            profile = self.client.get_user_details()
        except Exception as exc:
            self.disconnect()
            raise ShoonyaHistoryUnavailable(
                f"Shoonya profile request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(profile, dict) or profile.get("stat") != "Ok":
            self.disconnect()
            raise ShoonyaHistoryUnavailable("Shoonya profile response is unsuccessful or invalid")
        actual_uid = str(profile.get("uid") or "").strip()
        actual_account_id = str(profile.get("actid") or "").strip()
        if not actual_uid or not actual_account_id:
            self.disconnect()
            raise ShoonyaHistoryUnavailable("Shoonya profile is missing uid or actid")
        if actual_uid != expected_uid or actual_account_id != expected_account_id:
            self.disconnect()
            raise ShoonyaHistoryUnavailable(
                "Shoonya authenticated UID/account does not match requested identity"
            )
        self.uid = actual_uid
        self.account_id = actual_account_id
        self.connected = True
        return {
            "broker": self.name,
            "connected": True,
            "uid": actual_uid,
            "account_id": actual_account_id,
        }

    @staticmethod
    def _rows(value: Any, endpoint: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ShoonyaHistoryUnavailable(
                f"Shoonya {endpoint} response is not a complete row list"
            )
        for row in value:
            if row.get("stat") != "Ok":
                raise ShoonyaHistoryUnavailable(
                    f"Shoonya {endpoint} contains an unsuccessful row"
                )
        return value

    @staticmethod
    def _text(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        raise ShoonyaHistoryUnavailable(
            f"Shoonya history row is missing required field: {'/'.join(keys)}"
        )

    @staticmethod
    def _integer(
        row: dict[str, Any], *, positive: bool, keys: tuple[str, ...]
    ) -> int:
        value = next((row[key] for key in keys if row.get(key) not in (None, "")), None)
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            raise ShoonyaHistoryUnavailable(
                f"Shoonya history row has invalid numeric field: {'/'.join(keys)}"
            ) from None
        if parsed < (1 if positive else 0):
            raise ShoonyaHistoryUnavailable(
                f"Shoonya history row has invalid numeric field: {'/'.join(keys)}"
            )
        return parsed

    @staticmethod
    def _side(row: dict[str, Any]) -> str:
        side = ShoonyaAdapter._text(row, "trantype").upper()
        mapped = {"B": "BUY", "S": "SELL"}.get(side)
        if mapped is None:
            raise ShoonyaHistoryUnavailable("Shoonya order row has an invalid transaction side")
        return mapped

    def fetch_today_broker_snapshot(self, *, account_id: str) -> ShoonyaDailyHistory:
        if not self.connected or not self.uid or not self.account_id:
            raise ShoonyaHistoryUnavailable("Shoonya session is not connected")
        if account_id != self.account_id:
            raise ShoonyaHistoryUnavailable("Shoonya account scope does not match this session")
        try:
            order_rows = self._rows(self.client.get_order_book(), "order book")
            trade_rows = self._rows(self.client.get_trade_book(), "trade book")
        except ShoonyaHistoryUnavailable:
            raise
        except Exception as exc:
            raise ShoonyaHistoryUnavailable(
                f"Shoonya order/trade history request failed: {type(exc).__name__}"
            ) from exc

        orders = tuple(
            BrokerOrderSnapshot(
                broker_order_id=self._text(row, "norenordno"),
                symbol=self._text(row, "tsym"),
                side=self._side(row),
                quantity=self._integer(row, positive=True, keys=("qty",)),
                filled_quantity=self._integer(
                    row, positive=False, keys=("fillshares",)
                ),
                raw_status=str(row.get("status") or row.get("stat") or ""),
            )
            for row in order_rows
        )
        trades = tuple(
            BrokerTradeSnapshot(
                broker_trade_id=self._text(row, "flid"),
                broker_order_id=self._text(row, "norenordno"),
                quantity=self._integer(row, positive=True, keys=("flqty",)),
                fill_price=parse_positive_trade_price(row.get("flprc")),
                occurred_at_ns=parse_trade_timestamp_ns(row.get("fltm")),
            )
            for row in trade_rows
        )
        return ShoonyaDailyHistory(
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
        raise NotImplementedError("Shoonya live order routing is disabled pending Batch 0 safety approval")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Shoonya live order routing is disabled pending Batch 0 safety approval")

    def disconnect(self) -> None:
        self.uid = None
        self.account_id = None
        self.connected = False
        disconnect = getattr(self.client, "disconnect", None)
        if callable(disconnect):
            disconnect()
