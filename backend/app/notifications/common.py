"""Common strategy alert contract and channel dispatcher."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping
from app.core.config import settings
from app.core.database import SessionLocal
from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier
from app.notifications.telegram import TelegramConfig, TelegramNotifier
from app.notifications.email import EmailConfig, EmailNotifier
from app.models import AlertRule, AlertContact

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

def _ist_day_start_utc_naive(now=None):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    ist = current.astimezone(ZoneInfo("Asia/Kolkata"))
    ist_midnight = ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return ist_midnight.astimezone(timezone.utc).replace(tzinfo=None)

def _strictest_positive_limit(values):
    """Return the strictest configured positive limit; zero means unlimited."""
    positive = [value for value in values if value > 0]
    return min(positive) if positive else 0


def _finish_dispatch_transaction(dispatch_method):
    """Close dispatcher-owned read transactions without touching caller work."""
    def wrapped(self, db, event):
        caller_owned_transaction = db.in_transaction()
        try:
            return dispatch_method(self, db, event)
        finally:
            # dispatch() commits accepted paper mutations itself. Queries made
            # afterward (or an early fail-closed return) can otherwise leave a
            # fresh SQLAlchemy session in an implicit read transaction. Close
            # only transactions created by this dispatcher; never rollback a
            # transaction that was already active when the caller entered.
            if not caller_owned_transaction and db.in_transaction():
                db.rollback()
    return wrapped



_ALERT_OPERATORS = {
    ">=": lambda actual, target: actual >= target,
    ">": lambda actual, target: actual > target,
    "<=": lambda actual, target: actual <= target,
    "<": lambda actual, target: actual < target,
    "=": lambda actual, target: actual == target,
}
_ALERT_METADATA_KEYS = {
    "gap": ("gap",), "gross_profit": ("gross_profit", "gross_pnl", "gross_profit_rupees"),
    "net_profit": ("net_profit", "net_pnl"), "volume": ("volume",), "oi": ("oi", "open_interest"),
    "iv": ("iv", "implied_volatility"), "premium": ("premium", "option_premium"),
    "spread_value": ("spread_value", "spread"),
}

def _rule_matches_event(rule, event: AlertEvent) -> bool:
    """Evaluate the persisted metric/operator/threshold contract fail-closed."""
    try:
        import math
        operator = str(rule.operator)
        threshold = float(rule.threshold)
        if not math.isfinite(threshold) or operator not in _ALERT_OPERATORS:
            return False
        keys = _ALERT_METADATA_KEYS.get(str(rule.metric).lower())
        if not keys:
            return False
        actual = None
        for key in keys:
            if key in event.metadata and event.metadata[key] is not None:
                actual = float(event.metadata[key])
                break
        if actual is None or not math.isfinite(actual):
            return False
        return bool(_ALERT_OPERATORS[operator](actual, threshold))
    except (TypeError, ValueError, OverflowError):
        return False

def _valid_alert_rule(rule) -> bool:
    """Fail closed if persisted alert/risk configuration is malformed."""
    try:
        import math
        strategy_metrics = {
            "cash-future": {"gap", "gross_profit", "net_profit"},
            "calendar-spread": {"gap", "gross_profit"},
            "synthetic-future-cash-carry": {"gap", "gross_profit"},
            "box-spread": {"gap", "gross_profit"},
        }
        metric = str(getattr(rule, "metric", "gross_profit")).strip().lower()
        operator = str(getattr(rule, "operator", ">=")).strip()
        threshold = float(getattr(rule, "threshold", 0.0))
        strategy = str(getattr(rule, "strategy_id", "")).strip().lower()
        return (
            metric in strategy_metrics.get(strategy, set())
            and operator in {">=", ">", "<=", "<", "="}
            and math.isfinite(threshold)
            and math.isfinite(float(rule.min_gross_profit))
            and float(rule.min_gross_profit) >= 0
            and math.isfinite(float(rule.max_loss))
            and float(rule.max_loss) >= 0
            and math.isfinite(float(rule.max_daily_capital))
            and float(rule.max_daily_capital) >= 0
            and int(rule.max_simultaneous_positions) >= 1
            and math.isfinite(float(rule.cooldown_seconds))
            and float(rule.cooldown_seconds) >= 0
        )
    except (TypeError, ValueError, OverflowError):
        return False


class AlertService:
    """Single outbound alert service; disabled channels are safe no-ops."""
    def __init__(self, notifier: WhatsAppNotifier | None = None, email_notifier: EmailNotifier | None = None) -> None:
        self._notifier = notifier or WhatsAppNotifier(WhatsAppConfig(
            access_token=settings.WHATSAPP_ACCESS_TOKEN,
            phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
            graph_api_version=settings.WHATSAPP_GRAPH_API_VERSION,
            enabled=settings.WHATSAPP_ENABLED,
        ))
        self._telegram = TelegramNotifier(TelegramConfig(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            enabled=settings.TELEGRAM_ENABLED,
        ))
        self._email = email_notifier or EmailNotifier(EmailConfig(
            host=settings.EMAIL_SMTP_HOST,
            port=settings.EMAIL_SMTP_PORT,
            username=settings.EMAIL_SMTP_USERNAME,
            password=settings.EMAIL_SMTP_PASSWORD,
            from_address=settings.EMAIL_FROM_ADDRESS,
            enabled=settings.EMAIL_ENABLED,
            use_starttls=settings.EMAIL_SMTP_STARTTLS,
            use_ssl=settings.EMAIL_SMTP_SSL,
        ), timeout_seconds=settings.EMAIL_SMTP_TIMEOUT_SECONDS)
        self._last_sent: dict[tuple, int] = {}
        self._delivered_events: set[tuple] = set()

    @property
    def configured_channels(self) -> tuple[str, ...]:
        channels = []
        if self._notifier.configured:
            channels.append("whatsapp")
        if self._telegram.configured:
            channels.append("telegram")
        if self._email.configured:
            channels.append("email")
        return tuple(channels)

    def dispatch_user(self, user, event: AlertEvent) -> bool:
        # Compatibility notification path. Paper auto-entry is intentionally
        # owned by dispatch(), where enabled AlertRule risk gates, capital
        # reservation, max-loss and user isolation are enforced. This path is
        # also used by legacy scanner notifications and must never bypass those
        # controls by creating a paper position directly.
        return self._dispatch_notification_only(user, event)

    def _dispatch_notification_only(self, user, event: AlertEvent) -> bool:
        if not bool(getattr(user, "alerts_enabled", True)):
            return False
        key = (int(user.id), event.event_id)
        cooldown_ns = int(max(0.0, float(settings.LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS)) * 1_000_000_000)
        previous = self._last_sent.get(key, 0)
        if event.timestamp_ns - previous < cooldown_ns:
            return False
        subject = f"Algo Trading Alert: {event.strategy_id} / {event.symbol}"
        sent = False
        if bool(getattr(user, "whatsapp_alerts_enabled", True)) and getattr(user, "mobile_number", None):
            sent = self._notifier.send_text(user.mobile_number, event.message) or sent
        if bool(getattr(user, "email_alerts_enabled", True)) and getattr(user, "alert_email", None):
            sent = self._email.send_text(user.alert_email, subject, event.message) or sent
        if sent:
            self._last_sent[key] = event.timestamp_ns
        return sent

    @_finish_dispatch_transaction
    def dispatch(self, db, event: AlertEvent) -> int:
        gross = event.metadata.get("gross_profit", event.metadata.get("gross_pnl", event.metadata.get("gross_profit_rupees")))
        try:
            import math
            gross_value = float(gross) if gross is not None else None
            gross_valid = gross is None or math.isfinite(gross_value)
        except (TypeError, ValueError):
            gross_value = None
            gross_valid = gross is None
        rules = db.query(AlertRule).filter(
            AlertRule.enabled.is_(True),
            AlertRule.strategy_id == event.strategy_id.strip().lower(),
        ).order_by(AlertRule.priority.desc(), AlertRule.id.asc()).all()
        # DB constraints/Pydantic protect the normal API path, but persisted
        # rows can still be malformed after imports/manual DB edits. An invalid
        # risk rule is never allowed to become an implicit "unlimited" rule.
        rules = [rule for rule in rules if _valid_alert_rule(rule)]
        if rules:
            sent = 0
            from app.models import User
            users_by_id = {int(user.id): user for user in db.query(User).filter(User.id.in_({int(rule.user_id) for rule in rules})).all()}
            for rule in rules:
                user = users_by_id.get(int(rule.user_id))
                if user is None or not bool(getattr(user, "alerts_enabled", True)):
                    continue
                if gross_value is not None and gross_value < float(rule.min_gross_profit):
                    continue
                if not _rule_matches_event(rule, event):
                    continue
                whatsapp_enabled = bool(getattr(user, "whatsapp_alerts_enabled", True))
                telegram_enabled = bool(getattr(user, "telegram_alerts_enabled", True))
                contacts = db.query(AlertContact).filter(
                    AlertContact.user_id == int(rule.user_id),
                    AlertContact.enabled.is_(True),
                ).filter(
                    (AlertContact.whatsapp_enabled.is_(True)) |
                    (AlertContact.telegram_enabled.is_(True)) |
                    (AlertContact.email_enabled.is_(True))
                ).all()
                if not contacts and rule.whatsapp_enabled and rule.mobile_number.strip():
                    contacts = [rule]
                for contact in contacts:
                    cooldown_ns = int(max(0.0, float(rule.cooldown_seconds)) * 1_000_000_000)
                    channels = []
                    if whatsapp_enabled and bool(getattr(contact, "whatsapp_enabled", False)):
                        number = str(getattr(contact, "mobile_number", "")).strip()
                        if number:
                            channels.append(("whatsapp", number))
                    if telegram_enabled and bool(getattr(contact, "telegram_enabled", False)):
                        chat_id = str(getattr(contact, "telegram_chat_id", "")).strip()
                        if chat_id:
                            channels.append(("telegram", chat_id))
                    if bool(getattr(user, "email_alerts_enabled", True)) and bool(getattr(contact, "email_enabled", False)):
                        email = str(getattr(contact, "email_address", "")).strip()
                        if email:
                            channels.append(("email", email))
                    # Legacy AlertRule fallback remains WhatsApp-only because it
                    # predates persistent Telegram chat IDs.
                    if isinstance(contact, AlertRule) and rule.whatsapp_enabled and rule.mobile_number.strip():
                        channels = [("whatsapp", str(rule.mobile_number).strip())]
                    for channel, recipient in channels:
                        # event_id identifies the logical opportunity; timestamp_ns
                        # identifies the concrete market event. Dedup must include both:
                        # a new qualifying snapshot may reuse the same logical event_id
                        # after cooldown, while an identical snapshot must never resend.
                        dedup_key = (int(rule.id), event.event_id, int(event.timestamp_ns), channel, recipient)
                        cooldown_key = (int(rule.id), event.event_id, channel, recipient)
                        # Exact event delivery is always idempotent, including
                        # cooldown=0. Cooldown applies across later timestamps.
                        if dedup_key in self._delivered_events:
                            continue
                        previous = self._last_sent.get(cooldown_key)
                        if previous is not None and event.timestamp_ns - previous < cooldown_ns:
                            continue
                        try:
                            delivered = (
                                self._notifier.send_text(recipient, event.message)
                                if channel == "whatsapp"
                                else self._telegram.send_text(recipient, event.message)
                                if channel == "telegram"
                                else self._email.send_text(
                                    recipient,
                                    f"Algo Trading Alert: {event.strategy_id} / {event.symbol}",
                                    event.message,
                                )
                            )
                        except Exception as exc:
                            from app.core.logger import app_logger
                            app_logger.warning(
                                "Alert %s notification failed for rule %s: %s",
                                channel, rule.id, exc,
                            )
                            delivered = False
                        if delivered:
                            self._last_sent[cooldown_key] = event.timestamp_ns
                            self._delivered_events.add(dedup_key)
                            sent += 1
            return sent
        from app.models import User
        users = db.query(User).filter(User.is_active.is_(True), User.alerts_enabled.is_(True), User.mobile_number.isnot(None)).all()
        return sum(1 for user in users if self.dispatch_user(user, event))
    @staticmethod
    def cutoff(days: int) -> datetime:
        return datetime.utcnow() - timedelta(days=max(1, int(days)))

__all__ = ["AlertEvent", "AlertService"]
