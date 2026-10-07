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
        paper = event.metadata.get("paper_trade")
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
        if isinstance(paper, Mapping) and rules:
            # A supplied gross-profit value participates in the paper-entry
            # threshold. Never treat malformed/non-finite gross as "missing".
            if not gross_valid:
                return 0
            # Reject malformed numeric paper payloads before risk gates so
            # int()/float() coercion cannot silently change the requested
            # allocation that the gates are evaluating.
            try:
                lot_size_raw = float(paper.get("lot_size", 1))
                lots_raw = float(paper.get("lots", 1))
                edge_raw = float(paper.get("edge", 0.0))
                capital_raw = float(paper.get("capital_used", 0.0))
                import math
                if (
                    not all(math.isfinite(x) for x in (lot_size_raw, lots_raw, edge_raw, capital_raw))
                    or not lot_size_raw.is_integer()
                    or not lots_raw.is_integer()
                    or lot_size_raw <= 0
                    or lots_raw <= 0
                    or edge_raw < 0
                    or capital_raw <= 0
                ):
                    return 0
                lot_size_value = int(lot_size_raw)
                lots_value = int(lots_raw)
                edge_value = edge_raw
                capital_value = capital_raw
            except (TypeError, ValueError):
                return 0
            from app.auto.live_paper import LivePaperTradeService, _valid_persisted_trade
            from app.models.live_paper_trade import LivePaperTrade
            service = LivePaperTradeService()
            # Resolve due expiries before position/capital/loss gates. The
            # background monitor is periodic, so an alert can arrive at the
            # exact expiry boundary before that monitor gets a turn.
            #
            # Expiry processing owns its own durable transaction. Committing
            # through the caller session here could accidentally commit unrelated
            # pending work that the caller deliberately has not committed yet.
            # A later per-user risk rejection must also not be able to rollback
            # an expiry release that has already been accepted.
            expiry_db = SessionLocal()
            try:
                service.close_expired(expiry_db, commit=True)
            except Exception as exc:
                # Expiry cleanup owns a separate SQLite transaction. If that
                # transaction cannot acquire the database lock, risk gates must
                # fail closed rather than evaluate stale ongoing capital/loss
                # state and potentially create an unsafe new position.
                expiry_db.rollback()
                db.rollback()
                from app.core.logger import app_logger
                app_logger.warning("Live paper expiry cleanup unavailable; rejecting alert dispatch: %s", exc)
                return 0
            finally:
                expiry_db.close()
            eligible_rules = [
                rule for rule in rules
                if (gross_value is None or gross_value >= float(rule.min_gross_profit))
                and _rule_matches_event(rule, event)
            ]
            for rule_user_id in {int(rule.user_id) for rule in rules}:
                user_rules = [rule for rule in eligible_rules if int(rule.user_id) == rule_user_id]
                existing_trade = db.query(LivePaperTrade).filter(
                    LivePaperTrade.user_id == rule_user_id,
                    LivePaperTrade.event_id == str(event.event_id),
                    LivePaperTrade.status == "ONGOING",
                ).first()
                if existing_trade is not None:
                    try:
                        service.enter_or_mark(
                            db, strategy_id=event.strategy_id, symbol=event.symbol, event_id=event.event_id,
                            direction=paper.get("direction", "LONG"), expiry=paper.get("expiry"),
                            earliest_expiry=paper.get("earliest_expiry") or paper.get("expiry"),
                            lot_size=lot_size_value, lots=lots_value,
                            edge=edge_value, capital_used=0.0,
                            legs=paper.get("legs") or [], metadata=dict(paper), user_id=rule_user_id,
                        )
                        # mark() intentionally participates in the caller's
                        # transaction. Commit the duplicate mark here so a
                        # later user's risk rejection/rollback cannot undo this
                        # user's already-accepted P&L update.
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        from app.core.logger import app_logger
                        app_logger.error("Live paper duplicate mark failed for user %s: %s", rule_user_id, exc)
                    continue
                if not user_rules:
                    continue
                # Position/capital/loss limits are user-global, not strategy-local.
                # The current event must satisfy this strategy's gross-profit
                # threshold, but an enabled rule on another strategy still
                # consumes the same user's shared paper capital, daily budget,
                # loss budget, and simultaneous-position slots.
                global_user_rules = [
                    rule for rule in db.query(AlertRule).filter(
                        AlertRule.enabled.is_(True),
                        AlertRule.user_id == rule_user_id,
                    ).all()
                    if _valid_alert_rule(rule)
                ]
                if not global_user_rules:
                    db.rollback()
                    continue
                # A malformed persisted trade is not safe to ignore: doing so
                # could release an unknown capital reservation or loss from the
                # risk calculation. Fail closed for this user until the ledger row
                # is repaired.
                user_trades = db.query(LivePaperTrade).filter(
                    LivePaperTrade.user_id == rule_user_id,
                ).all()
                if any(not _valid_persisted_trade(trade) for trade in user_trades):
                    db.rollback()
                    continue
                from app.models.global_paper_setting import GlobalPaperSetting
                risk_lock = db.query(GlobalPaperSetting).filter(
                    GlobalPaperSetting.user_id == rule_user_id,
                ).update(
                    {GlobalPaperSetting.paper_amount: GlobalPaperSetting.paper_amount},
                    synchronize_session=False,
                )
                if risk_lock == 0:
                    # The lookup above starts a session transaction even when
                    # this user has no GlobalPaperSetting. Roll it back before
                    # evaluating another user so a read transaction from an
                    # unconfigured user cannot leak into the next user's
                    # risk-lock/write transaction.
                    db.rollback()
                    continue

                max_simultaneous = _strictest_positive_limit(
                    max(0, int(rule.max_simultaneous_positions)) for rule in global_user_rules
                )
                ongoing_count = db.query(LivePaperTrade).filter(
                    LivePaperTrade.user_id == rule_user_id,
                    LivePaperTrade.status == "ONGOING",
                ).count()
                if max_simultaneous and ongoing_count >= max_simultaneous:
                    db.rollback()
                    continue
                requested_capital = capital_value
                effective_capital = requested_capital
                effective_lots = lots_value
                if requested_capital > 0:
                    requested_lots = lots_value
                    capital_per_lot = requested_capital / requested_lots
                    if capital_per_lot > 0:
                        setting = db.query(GlobalPaperSetting).filter(
                            GlobalPaperSetting.user_id == rule_user_id,
                        ).first()
                        if setting is not None:
                            reserved_capital = sum(
                                float(trade.capital_used)
                                for trade in db.query(LivePaperTrade).filter(
                                    LivePaperTrade.user_id == rule_user_id,
                                    LivePaperTrade.status == "ONGOING",
                                ).all()
                                if _valid_persisted_trade(trade)
                            )
                            available_capital = max(0.0, float(setting.paper_amount) - reserved_capital)
                            # Monetary values are persisted as floats for compatibility,
                            # but binary float floor-division can under-allocate an exact
                            # boundary (e.g. 60_000 / 30_000 represented just below 2).
                            # Use Decimal from the displayed float values so exact-capital
                            # boundaries allocate the mathematically available whole lots.
                            from decimal import Decimal, ROUND_FLOOR
                            available_decimal = Decimal(str(available_capital))
                            per_lot_decimal = Decimal(str(capital_per_lot))
                            allocatable_by_capital = int(
                                (available_decimal / per_lot_decimal).to_integral_value(
                                    rounding=ROUND_FLOOR
                                )
                            )
                            allocatable_lots = min(
                                requested_lots,
                                max(0, allocatable_by_capital),
                            )
                            effective_lots = allocatable_lots
                            effective_capital = capital_per_lot * allocatable_lots
                day_start = _ist_day_start_utc_naive()
                max_daily_capital = _strictest_positive_limit(
                    max(0.0, float(rule.max_daily_capital)) for rule in global_user_rules
                )
                if max_daily_capital > 0 and effective_capital > 0:
                    daily_capital = sum(
                        float(trade.capital_used)
                        for trade in db.query(LivePaperTrade).filter(
                            LivePaperTrade.user_id == rule_user_id,
                            LivePaperTrade.status.in_(("ONGOING", "COMPLETED")),
                            LivePaperTrade.opened_at >= day_start,
                        ).all()
                        if _valid_persisted_trade(trade)
                    )
                    if daily_capital + effective_capital > max_daily_capital:
                        db.rollback()
                        continue
                max_loss = _strictest_positive_limit(
                    max(0.0, float(rule.max_loss)) for rule in global_user_rules
                )
                if max_loss > 0:
                    open_loss = sum(
                        min(0.0, float(trade.unrealized_pnl))
                        for trade in db.query(LivePaperTrade).filter(
                            LivePaperTrade.user_id == rule_user_id,
                            LivePaperTrade.status == "ONGOING",
                        ).all()
                        if _valid_persisted_trade(trade)
                    )
                    today_loss = sum(
                        min(0.0, float(trade.realized_pnl))
                        for trade in db.query(LivePaperTrade).filter(
                            LivePaperTrade.user_id == rule_user_id,
                            LivePaperTrade.status == "COMPLETED",
                            LivePaperTrade.closed_at >= day_start,
                        ).all()
                        if _valid_persisted_trade(trade)
                    )
                    if open_loss + today_loss <= -max_loss:
                        db.rollback()
                        continue
                try:
                    created_trade, created = service.enter_or_mark(
                        db, strategy_id=event.strategy_id, symbol=event.symbol, event_id=event.event_id,
                        direction=paper.get("direction", "LONG"), expiry=paper.get("expiry"),
                        earliest_expiry=paper.get("earliest_expiry") or paper.get("expiry"),
                        lot_size=lot_size_value, lots=effective_lots,
                        edge=edge_value, capital_used=effective_capital,
                        legs=paper.get("legs") or [], metadata=dict(paper), user_id=rule_user_id,
                    )
                    # Each user's accepted paper mutation is its own durable
                    # transaction boundary. Without this commit, a later
                    # user's risk rejection could rollback an earlier user's
                    # successful entry/mark in the shared dispatcher session.
                    #
                    # enter_or_mark() can also fail closed by returning
                    # (None, False) after the dispatcher has acquired the
                    # per-user risk lock (for example, disabled/emergency-stop
                    # settings or downstream validation). That path must
                    # explicitly rollback the lock transaction; otherwise the
                    # caller can retain an open write transaction/SQLite lock
                    # and the next alert can inherit a dirty transaction.
                    if created_trade is not None:
                        db.commit()
                    else:
                        db.rollback()
                except Exception as exc:
                    # enter_or_mark() can fail after this per-user risk lock has
                    # started a transaction. Roll back the failed attempt so a
                    # later alert cannot inherit a dirty transaction or retain
                    # the database write lock.
                    db.rollback()
                    from app.core.logger import app_logger
                    app_logger.error("Live paper auto-entry failed for user %s: %s", rule_user_id, exc)
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
        # A paper-trade payload is executed only through an enabled AlertRule,
        # where per-user risk gates are enforced. If no rule is configured,
        # keep the legacy fallback notification-only so a missing/disabled
        # rule cannot silently create a paper position outside those gates.
        if isinstance(paper, Mapping):
            return sum(1 for user in users if self._dispatch_notification_only(user, event))
        return sum(1 for user in users if self.dispatch_user(user, event))
    @staticmethod
    def cutoff(days: int) -> datetime:
        return datetime.utcnow() - timedelta(days=max(1, int(days)))

__all__ = ["AlertEvent", "AlertService"]
