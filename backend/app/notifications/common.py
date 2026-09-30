"""Common strategy alert contract and channel dispatcher."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping
from app.core.config import settings
from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier

@dataclass(frozen=True)
class AlertEvent:
    strategy_id: str
    event_id: str
    symbol: str
    timestamp_ns: int
    message: str
    observed_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> tuple[str, str, str, int]:
        return (self.strategy_id, self.symbol.upper(), self.event_id, self.timestamp_ns)

class AlertService:
    """Single outbound alert service. Disabled providers are safe no-ops."""
    def __init__(self, notifier: WhatsAppNotifier | None = None) -> None:
        self._notifier = notifier or WhatsAppNotifier(WhatsAppConfig(
            access_token=settings.WHATSAPP_ACCESS_TOKEN,
            phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
            graph_api_version=settings.WHATSAPP_GRAPH_API_VERSION,
            enabled=settings.WHATSAPP_ENABLED,
        ))

    @property
    def configured_channels(self) -> tuple[str, ...]:
        return ("whatsapp",) if self._notifier.configured else ()

    def dispatch(self, db, event: AlertEvent) -> int:
        if not self._notifier.configured:
            return 0
        from app.models import User
        sent = 0
        users = db.query(User).filter(User.is_active.is_(True), User.mobile_number.isnot(None)).all()
        for user in users:
            if self._notifier.send_text(user.mobile_number, event.message):
                sent += 1
        return sent

    @staticmethod
    def cutoff(days: int) -> datetime:
        return datetime.utcnow() - timedelta(days=max(1, int(days)))

    @staticmethod
    def retention_days() -> int:
        return 90

__all__ = ["AlertEvent", "AlertService"]
