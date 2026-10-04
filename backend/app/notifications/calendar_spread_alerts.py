"""Calendar Spread adapter for the common alert service."""
from __future__ import annotations
from datetime import datetime
from app.core.config import settings
from app.models import LiveCalendarSpreadAlertHistory
from app.notifications.common import AlertEvent, AlertService

class CalendarSpreadAlertService:
    def __init__(self, alerts: AlertService | None = None) -> None:
        self.alerts = alerts or AlertService()

    @staticmethod
    def _message(signal) -> str:
        return (
            "CALENDAR SPREAD ALERT\n"
            f"{signal.underlying} {signal.exchange}\n"
            f"Near/Far: {signal.near_contract_month}/{signal.far_contract_month}\n"
            f"Direction: {signal.direction}\n"
            f"Near Bid/Ask: ₹{signal.near_bid:.2f}/₹{signal.near_ask:.2f} | "
            f"Far Bid/Ask: ₹{signal.far_bid:.2f}/₹{signal.far_ask:.2f}\n"
            f"Executable Edge: ₹{signal.gap_points:.4f} | Gross P&L: ₹{signal.gross_profit:.2f}"
        )

    def persist(self, db, signal) -> bool:
        if not signal.qualifies:
            return False
        cutoff = self.alerts.cutoff(settings.LIVE_CALENDAR_SPREAD_RESULT_RETENTION_DAYS)
        db.query(LiveCalendarSpreadAlertHistory).filter(
            LiveCalendarSpreadAlertHistory.observed_at < cutoff
        ).delete(synchronize_session="fetch")
        db.flush()
        exists = db.query(LiveCalendarSpreadAlertHistory.id).filter(
            LiveCalendarSpreadAlertHistory.underlying == signal.underlying,
            LiveCalendarSpreadAlertHistory.exchange == signal.exchange,
            LiveCalendarSpreadAlertHistory.near_contract_month == signal.near_contract_month,
            LiveCalendarSpreadAlertHistory.far_contract_month == signal.far_contract_month,
            LiveCalendarSpreadAlertHistory.timestamp_ns == signal.timestamp_ns,
        ).first()
        if exists:
            return False
        db.add(LiveCalendarSpreadAlertHistory(
            observed_at=datetime.utcnow(),
            timestamp_ns=signal.timestamp_ns, underlying=signal.underlying,
            exchange=signal.exchange, near_contract_month=signal.near_contract_month,
            far_contract_month=signal.far_contract_month, direction=signal.direction,
            edge_long=signal.edge_long, edge_short=signal.edge_short,
            gap_points=signal.gap_points, gross_profit=signal.gross_profit,
            lot_size=signal.lot_size,
        ))
        db.commit()
        return True

    def emit(self, db, signal) -> int:
        if not signal.qualifies:
            return 0
        return self.alerts.dispatch(db, AlertEvent(
            strategy_id="calendar-spread",
            event_id=f"{signal.underlying}:{signal.near_contract_month}:{signal.far_contract_month}:{signal.direction}",
            symbol=signal.underlying, timestamp_ns=signal.timestamp_ns,
            message=self._message(signal),
            metadata={"exchange": signal.exchange, "gap_points": signal.gap_points, "gross_profit": signal.gross_profit, "paper_trade": {"exchange": signal.exchange, "direction": signal.direction, "expiry": signal.far_contract_month, "earliest_expiry": signal.near_contract_month, "lot_size": signal.lot_size, "lots": max(1, int(signal.capacity_lots or 1)), "edge": signal.gap_points, "capital_used": (signal.near_ask + signal.far_bid) * signal.lot_size * max(1, int(signal.capacity_lots or 1)), "legs": [{"contract": signal.near_contract_month, "side": "BUY" if signal.direction == "LONG" else "SELL", "price": signal.near_ask if signal.direction == "LONG" else signal.near_bid}, {"contract": signal.far_contract_month, "side": "SELL" if signal.direction == "LONG" else "BUY", "price": signal.far_bid if signal.direction == "LONG" else signal.far_ask}]}, },
        ))
