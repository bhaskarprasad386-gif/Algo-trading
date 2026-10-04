"""Common strategy alert contract and channel dispatcher."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping
from app.core.config import settings
from app.core.database import SessionLocal
from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier
from app.models import AlertRule

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


def _valid_alert_rule(rule) -> bool:
    """Fail closed if persisted risk configuration is malformed."""
    try:
        import math
        return (
            math.isfinite(float(rule.min_gross_profit))
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
    def __init__(self, notifier: WhatsAppNotifier | None = None) -> None:
        self._notifier = notifier or WhatsAppNotifier(WhatsAppConfig(
            access_token=settings.WHATSAPP_ACCESS_TOKEN,
            phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
            graph_api_version=settings.WHATSAPP_GRAPH_API_VERSION,
            enabled=settings.WHATSAPP_ENABLED,
        ))
        self._last_sent: dict[tuple[int, str], int] = {}

    @property
    def configured_channels(self) -> tuple[str, ...]:
        return ("whatsapp",) if self._notifier.configured else ()

    def dispatch_user(self, user, event: AlertEvent) -> bool:
        paper = event.metadata.get("paper_trade")
        if isinstance(paper, Mapping):
            paper_db = SessionLocal()
            try:
                from app.auto.live_paper import LivePaperTradeService
                service = LivePaperTradeService()
                service.close_expired(paper_db)
                service.enter_or_mark(
                    paper_db, strategy_id=event.strategy_id, symbol=event.symbol, event_id=event.event_id,
                    direction=paper.get("direction", "LONG"), expiry=paper.get("expiry"),
                    earliest_expiry=paper.get("earliest_expiry") or paper.get("expiry"),
                    lot_size=int(paper.get("lot_size", 1) or 1), lots=int(paper.get("lots", 1) or 1),
                    edge=float(paper.get("edge", 0.0) or 0.0), capital_used=float(paper.get("capital_used", 0.0) or 0.0),
                    legs=paper.get("legs") or [], metadata=dict(paper), user_id=int(user.id),
                )
            except Exception as exc:
                from app.core.logger import app_logger
                app_logger.error("Live paper auto-entry failed: %s", exc)
            finally:
                paper_db.close()
        if not user.mobile_number:
            return False
        key = (int(user.id), event.event_id)
        cooldown_ns = int(max(0.0, float(settings.LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS)) * 1_000_000_000)
        previous = self._last_sent.get(key, 0)
        if event.timestamp_ns - previous < cooldown_ns:
            return False
        sent = self._notifier.send_text(user.mobile_number, event.message)
        if sent:
            self._last_sent[key] = event.timestamp_ns
        return sent

    def _dispatch_notification_only(self, user, event: AlertEvent) -> bool:
        if not user.mobile_number:
            return False
        key = (int(user.id), event.event_id)
        cooldown_ns = int(max(0.0, float(settings.LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS)) * 1_000_000_000)
        previous = self._last_sent.get(key, 0)
        if event.timestamp_ns - previous < cooldown_ns:
            return False
        sent = self._notifier.send_text(user.mobile_number, event.message)
        if sent:
            self._last_sent[key] = event.timestamp_ns
        return sent

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
            service.close_expired(db)
            eligible_rules = [
                rule for rule in rules
                if gross_value is None or gross_value >= float(rule.min_gross_profit)
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
                    except Exception as exc:
                        from app.core.logger import app_logger
                        app_logger.error("Live paper duplicate mark failed for user %s: %s", rule_user_id, exc)
                    continue
                if not user_rules:
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
                    continue

                max_simultaneous = _strictest_positive_limit(
                    max(0, int(rule.max_simultaneous_positions)) for rule in user_rules
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
                            allocatable_lots = min(
                                requested_lots,
                                int(available_capital // capital_per_lot),
                            )
                            effective_capital = capital_per_lot * allocatable_lots
                day_start = _ist_day_start_utc_naive()
                max_daily_capital = _strictest_positive_limit(
                    max(0.0, float(rule.max_daily_capital)) for rule in user_rules
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
                    max(0.0, float(rule.max_loss)) for rule in user_rules
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
                    service.enter_or_mark(
                        db, strategy_id=event.strategy_id, symbol=event.symbol, event_id=event.event_id,
                        direction=paper.get("direction", "LONG"), expiry=paper.get("expiry"),
                        earliest_expiry=paper.get("earliest_expiry") or paper.get("expiry"),
                        lot_size=lot_size_value, lots=lots_value,
                        edge=edge_value, capital_used=capital_value,
                        legs=paper.get("legs") or [], metadata=dict(paper), user_id=rule_user_id,
                    )
                except Exception as exc:
                    from app.core.logger import app_logger
                    app_logger.error("Live paper auto-entry failed for user %s: %s", rule_user_id, exc)
        if rules:
            sent = 0
            for rule in rules:
                if not rule.whatsapp_enabled or not rule.mobile_number.strip():
                    continue
                if gross_value is not None and gross_value < float(rule.min_gross_profit):
                    continue
                key = (int(rule.id), event.event_id)
                cooldown_ns = int(max(0.0, float(rule.cooldown_seconds)) * 1_000_000_000)
                previous = self._last_sent.get(key)
                if previous is not None and event.timestamp_ns - previous < cooldown_ns:
                    continue
                if self._notifier.send_text(rule.mobile_number, event.message):
                    self._last_sent[key] = event.timestamp_ns
                    sent += 1
            return sent
        from app.models import User
        users = db.query(User).filter(User.is_active.is_(True), User.mobile_number.isnot(None)).all()
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
