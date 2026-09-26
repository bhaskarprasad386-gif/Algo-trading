"""Low-latency in-process Cash-Future scanner fed directly by the 1-second stream."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import math
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

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
    cash_bid_qty: float | None
    cash_ask_qty: float | None
    future_bid_qty: float | None
    future_ask_qty: float | None
    liquidity_qty: float | None
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
    capacity_lots: int | None
    capacity_notional: float | None
    lifecycle: str
    reason_codes: tuple[str, ...]
    observation_ref: str


class LiveCashFutureScanner:
    """Pairs same-second cash/future observations without broker quote polling."""

    def __init__(self, *, notifier: NotificationService | None = None) -> None:
        self.notifier = notifier or NotificationService()
        self._lock = threading.Lock()
        self._latest: dict[tuple[str, int], dict[str, dict]] = {}
        self._signals: dict[tuple[str, str], LiveCashFutureSignal] = {}
        self._session_extremes: dict[tuple[str, str], dict[str, float]] = {}
        self._stability: dict[tuple[str, str], tuple[int, int]] = {}
        self._alert_state: dict[tuple[str, str], str] = {}
        self._alert_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cf-alert")

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
    def _positive_qty(payload: dict, key: str) -> float | None:
        value = payload.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        return number

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

    def _notify_users(self, session_factory, signal: LiveCashFutureSignal) -> None:
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
                    self.notifier.notify_user(user, alert)
        except Exception:
            return

    @staticmethod
    def _factor(value: float | None, values: list[float]) -> float:
        if value is None or not values:
            return 0.0
        low, high = min(values), max(values)
        if high <= low:
            return 1.0
        return (value - low) / (high - low)

    def _rank(self, signals: list[LiveCashFutureSignal]) -> dict[tuple[str, str], float]:
        if not signals:
            return {}
        gaps = [max(0.0, s.gap_pct) for s in signals]
        nets = [max(0.0, s.net_gap_pct) for s in signals]
        annualized = [max(0.0, s.annualized_gap_pct or 0.0) for s in signals]
        stability = [float(s.stable_observations) for s in signals]
        liquidity = [float(s.liquidity_qty or 0.0) for s in signals]
        weights = (
            max(0.0, float(settings.LIVE_CASH_FUTURE_RANK_GAP_WEIGHT)),
            max(0.0, float(settings.LIVE_CASH_FUTURE_RANK_NET_WEIGHT)),
            max(0.0, float(settings.LIVE_CASH_FUTURE_RANK_ANNUALIZED_WEIGHT)),
            max(0.0, float(settings.LIVE_CASH_FUTURE_RANK_STABILITY_WEIGHT)),
            max(0.0, float(settings.LIVE_CASH_FUTURE_RANK_LIQUIDITY_WEIGHT)),
        )
        total = sum(weights) or 1.0
        return {
            (s.symbol, s.contract_month): (
                weights[0] * self._factor(max(0.0, s.gap_pct), gaps)
                + weights[1] * self._factor(max(0.0, s.net_gap_pct), nets)
                + weights[2] * self._factor(max(0.0, s.annualized_gap_pct or 0.0), annualized)
                + weights[3] * self._factor(float(s.stable_observations), stability)
                + weights[4] * self._factor(float(s.liquidity_qty or 0.0), liquidity)
            ) / total
            for s in signals
        }

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

        bid = self._price(payload, "bid")
        ask = self._price(payload, "ask")
        bid_qty = self._positive_qty(payload, "bid_qty")
        ask_qty = self._positive_qty(payload, "ask_qty")
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
            bucket[leg] = {
                "ltp": ltp, "bid": bid, "ask": ask,
                "bid_qty": bid_qty, "ask_qty": ask_qty,
                "lot_size": lot, "expiry": payload.get("expiry"),
            }
            self._latest = {
                k: v for k, v in self._latest.items()
                if k[1] >= timestamp_ns - 2_000_000_000
            }
            if "CASH" not in bucket or "FUTURE" not in bucket:
                return None
            cash, future = bucket["CASH"], bucket["FUTURE"]

        cash_ask, future_bid = cash["ask"], future["bid"]
        if cash_ask is None or future_bid is None:
            return None

        liquidity_values = [
            value for value in (
                cash["bid_qty"], cash["ask_qty"],
                future["bid_qty"], future["ask_qty"],
            ) if value is not None
        ]
        liquidity_qty = min(liquidity_values) if liquidity_values else None
        if liquidity_qty is not None and liquidity_qty < max(0, int(settings.LIVE_CASH_FUTURE_MIN_LIQUIDITY_QTY)):
            return None
        if liquidity_qty is None and int(settings.LIVE_CASH_FUTURE_MIN_LIQUIDITY_QTY) > 0:
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
            previous_stability = self._stability.get(stability_key)
            stable = (
                previous_stability[0] + 1
                if previous_stability and timestamp_ns == previous_stability[1] + 1_000_000_000
                else 1
            )
            self._stability[stability_key] = (stable, timestamp_ns)
            previous_signal = self._signals.get((symbol, month))

        estimated_cost = max(0.0, float(settings.LIVE_CASH_FUTURE_ESTIMATED_COST_PER_LOT))
        estimated_cost += max(0.0, float(settings.LIVE_CASH_FUTURE_SLIPPAGE_PER_LOT))
        gross_lot_value = gap * lot if lot else None
        net_gap = gap - estimated_cost / lot if lot else gap
        net_gap_pct = net_gap / cash_ask * 100.0
        annualized = gap_pct * 365.0 / days if days else None

        capacity_lots = None
        capacity_notional = None
        if lot and lot > 0 and cash_ask > 0:
            capacity_lots = max(0, int(float(settings.LIVE_CASH_FUTURE_CAPITAL) // (cash_ask * lot)))
            capacity_notional = capacity_lots * cash_ask * lot

        min_stable = max(1, int(settings.LIVE_CASH_FUTURE_MIN_STABLE_OBSERVATIONS))
        eligible = (
            gap > 0
            and net_gap > 0
            and gap_pct >= float(settings.LIVE_CASH_FUTURE_ALERT_MIN_GAP_PCT)
            and stable >= min_stable
        )
        if previous_signal is None:
            lifecycle = "NEW" if eligible else "EXPIRED"
        elif eligible and previous_signal.gap > 0 and gap < previous_signal.gap:
            lifecycle = "WEAKENING"
        elif eligible:
            lifecycle = "ACTIVE"
        else:
            lifecycle = "EXPIRED"

        reasons = ["TWO_SIDED_EXECUTABLE_QUOTE"]
        if gap > 0:
            reasons.append("POSITIVE_EXECUTABLE_SPREAD")
        if net_gap > 0:
            reasons.append("POSITIVE_NET_GAP")
        if stable >= min_stable:
            reasons.append("STABLE")
        if liquidity_qty is not None:
            reasons.append("LIQUIDITY_MEASURED")
        if capacity_lots is not None:
            reasons.append("CAPACITY_ESTIMATED")

        signal = LiveCashFutureSignal(
            symbol=symbol,
            contract_month=month,
            cash_ltp=cash["ltp"],
            future_ltp=future["ltp"],
            cash_bid=cash["bid"],
            cash_ask=cash_ask,
            future_bid=future_bid,
            future_ask=future["ask"],
            cash_bid_qty=cash["bid_qty"],
            cash_ask_qty=cash["ask_qty"],
            future_bid_qty=future["bid_qty"],
            future_ask_qty=future["ask_qty"],
            liquidity_qty=liquidity_qty,
            gap=gap,
            gap_pct=gap_pct,
            timestamp_ns=timestamp_ns,
            cash_day_high=ext["cash_high"],
            cash_day_low=ext["cash_low"],
            future_day_high=ext["future_high"],
            future_day_low=ext["future_low"],
            lot_size=lot,
            gross_lot_value=gross_lot_value,
            estimated_cost=estimated_cost,
            net_gap=net_gap,
            net_gap_pct=net_gap_pct,
            annualized_gap_pct=annualized,
            stable_observations=stable,
            capacity_lots=capacity_lots,
            capacity_notional=capacity_notional,
            lifecycle=lifecycle,
            reason_codes=tuple(reasons),
            observation_ref=f"{symbol}:{month}:{timestamp_ns}",
        )
        with self._lock:
            self._signals[(signal.symbol, signal.contract_month)] = signal
            state_key = (signal.symbol, signal.contract_month)
            previous_alert_state = self._alert_state.get(state_key)
            if eligible and previous_alert_state != "ACTIVE":
                alert_state = "RECOVERY" if previous_alert_state else "NEW"
                self._alert_state[state_key] = "ACTIVE"
            elif not eligible:
                self._alert_state[state_key] = "WEAKENING" if previous_alert_state == "ACTIVE" else "INACTIVE"
                alert_state = None
            else:
                alert_state = None

        if alert_state and session_factory is not None:
            self._alert_executor.submit(self._notify_users, session_factory, signal)
        return signal

    def snapshot(self, *, max_age_seconds: float = 5.0, limit: int = 50) -> list[dict]:
        now_ns = int(datetime.now(IST).timestamp() * 1_000_000_000)
        cutoff = int(now_ns - max_age_seconds * 1_000_000_000)
        with self._lock:
            signals = [signal for signal in self._signals.values() if signal.timestamp_ns >= cutoff]
        signals.sort(key=lambda item: (-item.gap_pct, item.symbol, item.contract_month))
        rank_scores = self._rank(signals)
        by_symbol: dict[str, list[LiveCashFutureSignal]] = {}
        for signal in signals:
            by_symbol.setdefault(signal.symbol, []).append(signal)

        data: list[dict] = []
        for signal in signals[:limit]:
            item = signal.__dict__.copy()
            peers = by_symbol.get(signal.symbol, [])
            peer = next((p for p in peers if p.contract_month != signal.contract_month), None)
            item["rank_score"] = rank_scores.get((signal.symbol, signal.contract_month), 0.0)
            item["rank_factors"] = {
                "gap_pct": signal.gap_pct,
                "net_gap_pct": signal.net_gap_pct,
                "annualized_gap_pct": signal.annualized_gap_pct,
                "stable_observations": signal.stable_observations,
                "liquidity_qty": signal.liquidity_qty,
            }
            item["peer_contract_month"] = peer.contract_month if peer else None
            item["peer_gap_pct"] = peer.gap_pct if peer else None
            item["gap_pct_delta_vs_peer"] = signal.gap_pct - peer.gap_pct if peer else None
            item["is_best_contract_month"] = bool(peer and signal.gap_pct >= peer.gap_pct)
            item["lifecycle"] = signal.lifecycle if signal.timestamp_ns >= cutoff else "EXPIRED"
            data.append(item)

        data.sort(key=lambda item: (-float(item["rank_score"]), -float(item["gap_pct"]), item["symbol"], item["contract_month"]))
        return data[:limit]
