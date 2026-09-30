"""Synthetic-arbitrage alert delivery and durable 90-day history."""

from __future__ import annotations
from datetime import datetime, timedelta
import threading
from app.core.config import settings
from app.models import LiveSyntheticAlertHistory, User
from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier
from app.notifications.common import AlertEvent, AlertService

class SyntheticAlertService:
    def __init__(self) -> None:
        self._alerts = AlertService()
        self._notifier = WhatsAppNotifier(WhatsAppConfig(
            access_token=settings.WHATSAPP_ACCESS_TOKEN,
            phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
            graph_api_version=settings.WHATSAPP_GRAPH_API_VERSION,
            enabled=settings.WHATSAPP_ENABLED,
        ))
        self._lock = threading.Lock()
        self._last_sent: dict[tuple[int, str, str], int] = {}

    @staticmethod
    def _message(result) -> str:
        o, f = result.option, result.future
        return (
            "SYNTHETIC ARBITRAGE ALERT\\n"
            f"{o.underlying} {o.instrument_class}\\n"
            f"Expiry: {o.expiry} | Strike: {o.strike:g}\\n"
            f"Direction: {result.direction}\\n"
            f"Future Bid/Ask: ₹{f.bid:.2f}/₹{f.ask:.2f}\\n"
            f"CE Bid/Ask: ₹{o.call_bid:.2f}/₹{o.call_ask:.2f}\\n"
            f"PE Bid/Ask: ₹{o.put_bid:.2f}/₹{o.put_ask:.2f}\\n"
            f"Executable Edge: ₹{result.executable_edge:.4f}\\n"
            f"Edge/Lot: ₹{result.edge_per_lot:.2f} | Gross P&L: ₹{result.gross_pnl:.2f}"
        )

    def notify_users(self, db, results) -> int:
        if not results or not self._notifier.configured:
            return 0
        sent = 0
        now = datetime.utcnow()
        for result in results:
            key_base = (result.option.underlying, result.direction)
            for user in db.query(User).filter(User.is_active.is_(True), User.mobile_number.isnot(None)).all():
                key = (int(user.id), *key_base)
                with self._lock:
                    previous = self._last_sent.get(key, 0)
                    cooldown_ns = int(float(getattr(settings, "LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS", 60.0)) * 1_000_000_000)
                    if result.option.timestamp_ns - previous < cooldown_ns:
                        continue
                if self._alerts.dispatch_user(user, AlertEvent(strategy_id="synthetic-future-cash-carry", event_id=f"{result.option.underlying}:{result.option.expiry}:{result.option.strike:g}:{result.direction}", symbol=result.option.underlying, timestamp_ns=result.option.timestamp_ns, message=self._message(result))):
                    with self._lock:
                        self._last_sent[key] = result.option.timestamp_ns
                    sent += 1
        return sent

    def persist(self, db, results) -> int:
        retention_days = max(1, int(settings.LIVE_SYNTHETIC_RESULT_RETENTION_DAYS))
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        db.query(LiveSyntheticAlertHistory).filter(LiveSyntheticAlertHistory.observed_at < cutoff).delete(synchronize_session=False)
        added = 0
        for result in results:
            o = result.option
            f = result.future
            exists = db.query(LiveSyntheticAlertHistory.id).filter(
                LiveSyntheticAlertHistory.symbol == o.underlying,
                LiveSyntheticAlertHistory.expiry == o.expiry,
                LiveSyntheticAlertHistory.timestamp_ns == o.timestamp_ns,
                LiveSyntheticAlertHistory.strike == o.strike,
                LiveSyntheticAlertHistory.direction == result.direction,
            ).first()
            if exists:
                continue
            db.add(LiveSyntheticAlertHistory(
                observed_at=datetime.utcnow(),
                timestamp_ns=o.timestamp_ns,
                symbol=o.underlying,
                instrument_class=o.instrument_class,
                expiry=o.expiry,
                strike=o.strike,
                direction=result.direction,
                executable_edge=result.executable_edge,
                edge_per_lot=result.edge_per_lot,
                gross_pnl=result.gross_pnl,
                lot_size=f.lot_size,
            ))
            added += 1
        db.commit()
        return added
