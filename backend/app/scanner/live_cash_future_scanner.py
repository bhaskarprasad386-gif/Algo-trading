"""Low-latency in-process Cash-Future scanner fed directly by the 1-second stream."""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
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
    cash_day_high: float
    cash_day_low: float
    future_day_high: float
    future_day_low: float
    lot_size: int | None
    gross_lot_value: float | None
    estimated_cost: float
    net_gap: float
    net_gap_pct: float
    annualized_gap_pct: float | None
    stable_observations: int


class LiveCashFutureScanner:
    """Pairs same-second cash/future observations without broker quote polling."""

    def __init__(self, *, notifier: NotificationService | None = None) -> None:
        self.notifier = notifier or NotificationService()
        self._lock = threading.Lock()
        self._latest: dict[tuple[str, int], dict[str, dict]] = {}
        self._signals: dict[tuple[str, str], LiveCashFutureSignal] = {}
        self._session_extremes: dict[tuple[str, str], dict[str, float]] = {}
        self._stability: dict[tuple[str, str], tuple[int, int]] = {}

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

    @staticmethod
    def _parse_expiry(value: object):
        if not value:
            return None
        for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(str(value).strip().upper(), fmt).date()
            except ValueError:
                continue
        return None

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
            ltp = self._price({"x": payload.get("last_traded_price")}, "x", 100.0)
        if ltp is None:
            return None
        bid, ask = self._price(payload, "bid"), self._price(payload, "ask")
        lot = None
        try:
            raw_lot = payload.get("lot_size")
            if raw_lot is not None and int(raw_lot) > 0:
                lot = int(raw_lot)
        except (TypeError, ValueError):
            pass

        key = (symbol, timestamp_ns)
        with self._lock:
            bucket = self._latest.setdefault(key, {})
            bucket[leg] = {"ltp": ltp, "bid": bid, "ask": ask, "lot_size": lot, "expiry": payload.get("expiry")}
            self._latest = {k: v for k, v in self._latest.items() if k[1] >= timestamp_ns - 2_000_000_000}
            if "CASH" not in bucket or "FUTURE" not in bucket:
                return None
            cash, future = bucket["CASH"], bucket["FUTURE"]

        cash_ask, future_bid = cash["ask"], future["bid"]
        if cash_ask is None or future_bid is None:
            return None
        gap = future_bid - cash_ask
        gap_pct = gap / cash_ask * 100.0
        ts_date = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, IST).date()
        expiry = self._parse_expiry(future.get("expiry"))
        days = max(1, (expiry - ts_date).days) if expiry else None
        extreme_key = (symbol, ts_date.isoformat())
        stability_key = (symbol, month)
        with self._lock:
            ext = self._session_extremes.setdefault(extreme_key, {
                "cash_high": cash["ltp"], "cash_low": cash["ltp"],
                "future_high": future["ltp"], "future_low": future["ltp"],
            })
            ext["cash_high"] = max(ext["cash_high"], cash["ltp"])
            ext["cash_low"] = min(ext["cash_low"], cash["ltp"])
            ext["future_high"] = max(ext["future_high"], future["ltp"])
            ext["future_low"] = min(ext["future_low"], future["ltp"])
            previous = self._stability.get(stability_key)
            stable = previous[0] + 1 if previous and timestamp_ns == previous[1] + 1_000_000_000 else 1
            self._stability[stability_key] = (stable, timestamp_ns)

        estimated_cost = max(0.0, float(settings.LIVE_CASH_FUTURE_ESTIMATED_COST_PER_LOT))
        estimated_cost += max(0.0, float(settings.LIVE_CASH_FUTURE_SLIPPAGE_PER_LOT))
        gross_lot_value = gap * lot if lot else None
        net_gap = gap - estimated_cost / lot if lot else gap
        net_gap_pct = net_gap / cash_ask * 100.0
        annualized = gap_pct * 365.0 / days if days else None
        signal = LiveCashFutureSignal(
            symbol=symbol, contract_month=month, cash_ltp=cash["ltp"], future_ltp=future["ltp"],
            cash_bid=cash["bid"], cash_ask=cash_ask, future_bid=future_bid, future_ask=future["ask"],
            gap=gap, gap_pct=gap_pct, timestamp_ns=timestamp_ns,
            cash_day_high=ext["cash_high"], cash_day_low=ext["cash_low"],
            future_day_high=ext["future_high"], future_day_low=ext["future_low"],
            lot_size=lot, gross_lot_value=gross_lot_value, estimated_cost=estimated_cost,
            net_gap=net_gap, net_gap_pct=net_gap_pct, annualized_gap_pct=annualized,
            stable_observations=stable,
        )
        with self._lock:
            self._signals[(signal.symbol, signal.contract_month)] = signal
        if gap > 0 and net_gap > 0 and gap_pct >= settings.LIVE_CASH_FUTURE_ALERT_MIN_GAP_PCT and stable >= max(1, int(settings.LIVE_CASH_FUTURE_MIN_STABLE_OBSERVATIONS)):
            self._notify_users(session_factory, signal)
        return signal

    def snapshot(self, *, max_age_seconds: float = 5.0, limit: int = 50) -> list[dict]:
        now_ns = datetime.now(IST).timestamp() * 1_000_000_000
        cutoff = int(now_ns - max_age_seconds * 1_000_000_000)
        with self._lock:
            signals = [signal for signal in self._signals.values() if signal.timestamp_ns >= cutoff]
        signals.sort(key=lambda item: (-item.gap_pct, item.symbol, item.contract_month))
        return [signal.__dict__.copy() for signal in signals[:limit]]
