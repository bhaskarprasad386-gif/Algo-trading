"""Low-latency in-process Cash-Future scanner fed directly by the 1-second stream."""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import User
from app.notifications.service import LiveCashFutureAlert, NotificationService

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class LiveCashFutureSignal:
    symbol: str
    contract_month: str
    cash_ltp: float
    future_ltp: float
    cash_bid: float | None
    cash_ask: float | None
    future_bid: float | None
    future_ask: float | None
    gap: float
    gap_pct: float
    timestamp_ns: int


class LiveCashFutureScanner:
    """Pairs same-second cash/future observations without broker quote polling."""

    def __init__(self, *, notifier: NotificationService | None = None) -> None:
        self.notifier = notifier or NotificationService()
        self._lock = threading.Lock()
        self._latest: dict[tuple[str, str, int], dict[str, dict]] = {}

    @staticmethod
    def _price(payload: dict, key: str, divisor: float = 1.0) -> float | None:
        value = payload.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        return number / divisor

    @staticmethod
    def _notify_users(session_factory, signal: LiveCashFutureSignal) -> None:
        if session_factory is None:
            return
        try:
            with session_factory() as db:
                users = db.query(User).filter(User.is_active.is_(True), User.mobile_number.isnot(None)).all()
                alert = LiveCashFutureAlert(
                    symbol=signal.symbol,
                    contract_month=signal.contract_month,
                    cash_ask=float(signal.cash_ask),
                    future_bid=float(signal.future_bid),
                    gap=signal.gap,
                    gap_pct=signal.gap_pct,
                    timestamp_ns=signal.timestamp_ns,
                )
                for user in users:
                    self._notification_user(user, alert)
        except Exception:
            return

    def _notification_user(self, user: User, alert: LiveCashFutureAlert) -> None:
        self.notifier.notify_user(user, alert)

    def observe(self, payload: dict, *, session_factory=None) -> LiveCashFutureSignal | None:
        leg = str(payload.get("leg") or "").upper()
        symbol = str(payload.get("underlying") or "").strip().upper()
        month = str(payload.get("contract_month") or "").strip().upper()
        timestamp_ns = int(payload.get("source_timestamp_ns") or payload.get("exchange_timestamp") or 0)
        if timestamp_ns <= 0 or not symbol:
            return None
        if leg == "CASH":
            month = "CASH"
        elif leg != "FUTURE" or not month:
            return None
        ltp = self._price(payload, "ltp")
        if ltp is None:
            raw = payload.get("last_traded_price")
            ltp = self._price({"x": raw}, "x", 100.0)
        if ltp is None:
            return None
        bid = self._price(payload, "bid")
        ask = self._price(payload, "ask")
        key = (symbol, month if leg == "FUTURE" else "ALL", timestamp_ns)
        with self._lock:
            bucket = self._latest.setdefault(key, {})
            bucket[leg] = {"ltp": ltp, "bid": bid, "ask": ask}
            if "CASH" not in bucket or "FUTURE" not in bucket:
                return None
            cash = bucket["CASH"]
            future = bucket["FUTURE"]
            self._latest = {k: v for k, v in self._latest.items() if k[2] >= timestamp_ns - 2_000_000_000}
        cash_ask = cash["ask"]
        future_bid = future["bid"]
        if cash_ask is None or future_bid is None or cash_ask <= 0 or future_bid <= 0:
            return None
        gap = future_bid - cash_ask
        gap_pct = gap / cash_ask * 100.0
        signal = LiveCashFutureSignal(
            symbol=symbol,
            contract_month=month,
            cash_ltp=cash["ltp"],
            future_ltp=future["ltp"],
            cash_bid=cash["bid"],
            cash_ask=cash_ask,
            future_bid=future_bid,
            future_ask=future["ask"],
            gap=gap,
            gap_pct=gap_pct,
            timestamp_ns=timestamp_ns,
        )
        if gap > 0 and gap_pct >= settings.LIVE_CASH_FUTURE_ALERT_MIN_GAP_PCT:
            self._notify_users(session_factory, signal)
        return signal
