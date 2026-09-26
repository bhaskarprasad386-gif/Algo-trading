"""Application notification service for live Cash-Future alerts."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from app.core.config import settings
from app.models import User
from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier


@dataclass(frozen=True)
class LiveCashFutureAlert:
    symbol: str
    contract_month: str
    cash_ask: float
    future_bid: float
    gap: float
    gap_pct: float
    timestamp_ns: int
    lot_size: int | None = None
    alert_lots: int | None = None
    gross_profit: float | None = None
    net_profit: float | None = None


class NotificationService:
    """Delivers live alerts without blocking the market-data callback."""

    def __init__(self) -> None:
        self._notifier = WhatsAppNotifier(
            WhatsAppConfig(
                access_token=settings.WHATSAPP_ACCESS_TOKEN,
                phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
                graph_api_version=settings.WHATSAPP_GRAPH_API_VERSION,
                enabled=settings.WHATSAPP_ENABLED,
            )
        )
        self._lock = threading.Lock()
        self._last_alert_ns: dict[tuple[int, str, str], int] = {}

    @staticmethod
    def _message(alert: LiveCashFutureAlert) -> str:
        return (
            "CASH-FUTURE ALERT\\n"
            f"{alert.symbol} {alert.contract_month}\\n"
            f"Cash Ask: ₹{alert.cash_ask:.2f}\\n"
            f"Future Bid: ₹{alert.future_bid:.2f}\\n"
            f"Spread: ₹{alert.gap:.2f} ({alert.gap_pct:.3f}%)\\n"\n            f"Lot Size: {alert.lot_size or 0} | Lots: {alert.alert_lots or 0}\\n"\n            f"Gross Profit: ₹{(alert.gross_profit or 0.0):.2f}\\n"\n            f"Net Profit: ₹{(alert.net_profit or 0.0):.2f}"
        )

    def notify_user(self, user: User, alert: LiveCashFutureAlert) -> bool:
        if not user.mobile_number:
            return False
        key = (int(user.id), alert.symbol.upper(), alert.contract_month.upper())
        cooldown_ns = int(settings.LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS * 1_000_000_000)
        with self._lock:
            previous = self._last_alert_ns.get(key, 0)
            if alert.timestamp_ns - previous < cooldown_ns:
                return False
        sent = self._notifier.send_text(user.mobile_number, self._message(alert))
        if sent:
            with self._lock:
                self._last_alert_ns[key] = alert.timestamp_ns
        return sent

    @property
    def whatsapp_configured(self) -> bool:
        return self._notifier.configured
