"""Compatibility wrapper routing Cash-Future alerts through the common alert service."""
from __future__ import annotations
from dataclasses import dataclass
from app.core.config import settings
from app.models import User
from app.notifications.common import AlertEvent, AlertService

@dataclass(frozen=True)
class LiveCashFutureAlert:
    symbol: str
    contract_month: str
    cash_ask: float
    future_bid: float
    gap: float
    gap_pct: float
    timestamp_ns: int
    expiry: str | None = None
    lot_size: int | None = None
    alert_lots: int | None = None
    gross_profit: float | None = None
    net_profit: float | None = None

class NotificationService:
    def __init__(self) -> None:
        self._common = AlertService()
        self._notifier = self._common._notifier
        self._last_alert_ns = self._common._last_sent

    @staticmethod
    def _message(alert: LiveCashFutureAlert) -> str:
        return (
            "CASH-FUTURE ALERT\\n"
            f"{alert.symbol} {alert.contract_month}\\n"
            f"Cash Ask: ₹{alert.cash_ask:.2f}\\n"
            f"Future Bid: ₹{alert.future_bid:.2f}\\n"
            f"Spread: ₹{alert.gap:.2f} ({alert.gap_pct:.3f}%)\\n"
            f"Lot Size: {alert.lot_size or 0} | Lots: {alert.alert_lots or 0}\\n"
            f"Gross Profit: ₹{(alert.gross_profit or 0.0):.2f}\\n"
            f"Net Profit: ₹{(alert.net_profit or 0.0):.2f}"
        )

    def notify_user(self, user: User, alert: LiveCashFutureAlert) -> bool:
        return self._common.dispatch_user(user, AlertEvent(
            strategy_id="cash-future",
            event_id=f"{alert.symbol}:{alert.contract_month}",
            symbol=alert.symbol,
            timestamp_ns=alert.timestamp_ns,
            message=self._message(alert),
            metadata={
                "gross_profit": alert.gross_profit,
                "paper_trade": {
                    "direction": "LONG", "expiry": alert.expiry or alert.contract_month,
                    "earliest_expiry": alert.expiry or alert.contract_month, "lot_size": int(alert.lot_size or 1),
                    "lots": int(alert.alert_lots or 1), "edge": float(alert.gap),
                    "capital_used": (float(alert.cash_ask) + float(alert.future_bid)) * int(alert.lot_size or 1) * int(alert.alert_lots or 1),
                    "legs": [{"instrument": "CASH", "side": "BUY", "price": alert.cash_ask}, {"instrument": "FUTURE", "side": "SELL", "price": alert.future_bid}],
                },
            },
        ))

    def dispatch(self, db, alert: LiveCashFutureAlert) -> int:
        return self._common.dispatch(db, AlertEvent(
            strategy_id="cash-future",
            event_id=f"{alert.symbol}:{alert.contract_month}",
            symbol=alert.symbol,
            timestamp_ns=alert.timestamp_ns,
            message=self._message(alert),
            metadata={
                "gross_profit": alert.gross_profit,
                "paper_trade": {
                    "direction": "LONG", "expiry": alert.expiry or alert.contract_month,
                    "earliest_expiry": alert.expiry or alert.contract_month,
                    "lot_size": int(alert.lot_size or 1), "lots": int(alert.alert_lots or 1),
                    "edge": float(alert.gap),
                    "capital_used": (float(alert.cash_ask) + float(alert.future_bid)) * int(alert.lot_size or 1) * int(alert.alert_lots or 1),
                    "legs": [{"instrument": "CASH", "side": "BUY", "price": alert.cash_ask}, {"instrument": "FUTURE", "side": "SELL", "price": alert.future_bid}],
                },
            },
        ))

    @property
    def whatsapp_configured(self) -> bool:
        return bool(self._common.configured_channels)
