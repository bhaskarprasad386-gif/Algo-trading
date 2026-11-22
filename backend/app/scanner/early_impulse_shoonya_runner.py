"""Fail-closed, synchronized 5-engine Early Impulse runner over observer ticks.

The runner evaluates only explicitly role-mapped instruments; it never guesses
which index/future is intended from a symbol or option descriptor.
"""
from __future__ import annotations

from collections import deque
from math import isfinite
from threading import RLock
from time import monotonic_ns
from typing import Any

from app.market_data.contracts import MarketDataRecord
from app.scanner.early_impulse_engine import evaluate_market_features
from app.scanner.early_impulse_synchronized_snapshot import TimedQuote, build_synchronized_snapshot
from app.scanner.early_impulse_signal_store import EarlyImpulseSignalStore

MAX_HISTORY = 120
MAX_ROLE_COUNT = 5


class EarlyImpulseLiveRunner:
    def __init__(self, *, signal_store: EarlyImpulseSignalStore | None = None,
                 max_age_seconds: float = 2.0, max_skew_ms: float = 500.0):
        if max_age_seconds <= 0 or max_skew_ms < 0:
            raise ValueError("age/skew limits invalid")
        self.store = signal_store or EarlyImpulseSignalStore()
        self.max_age_ns = int(max_age_seconds * 1e9)
        self.max_skew_ns = int(max_skew_ms * 1e6)
        self._lock = RLock()
        self._epoch: int | None = None
        self._authenticated = False
        self._roles: dict[str, str] = {}
        self._history: dict[str, deque[TimedQuote]] = {}
        self._last_volume: dict[str, int] = {}
        self._last_eval_ns = 0
        self._evaluations = 0
        self._aligned_evaluations = 0
        self._signals = 0
        self._last_result: dict[str, Any] | None = None
        self._enabled = False

    def configure_roles(self, roles: dict[str, str]) -> dict:
        allowed = {"spot", "future", "cross"}
        if not isinstance(roles, dict) or len(roles) > MAX_ROLE_COUNT:
            raise ValueError("role map must be a dictionary with at most 5 instruments")
        staged = {}
        for instrument, role in roles.items():
            key, val = str(instrument).strip(), str(role).strip().lower()
            if not key or val not in allowed or val in staged.values():
                raise ValueError("instrument names must be unique and each role (spot/future/cross) used once")
            staged[key] = val
        if not {"spot", "future", "cross"}.issubset(set(staged.values())):
            raise ValueError("role map must include spot, future and cross instruments")
        with self._lock:
            self._roles = staged
            self._history.clear()
            self._last_volume.clear()
            self._last_result = None
            self._last_eval_ns = 0
        return self.snapshot()

    def enable(self) -> dict:
        with self._lock:
            if not self._roles:
                raise ValueError("configure spot, future and cross instruments first")
            self._enabled = True
        return self.snapshot()

    def disable(self) -> dict:
        with self._lock:
            self._enabled = False
            self._history.clear()
            self._last_volume.clear()
            self._last_result = None
            self._last_eval_ns = 0
        return self.snapshot()

    def on_session(self, state: str, epoch: int, authenticated: bool, connected: bool) -> None:
        with self._lock:
            if self._epoch is not None and epoch < self._epoch:
                return
            changed = epoch != self._epoch
            self._epoch = epoch
            self._authenticated = bool(authenticated and connected and str(state).upper() == "LIVE")
            if changed or not connected or not self._authenticated:
                self._last_eval_ns = 0
                self._history.clear()
                self._last_volume.clear()
                self._last_result = None

    def on_tick(self, record: Any, epoch: int, authenticated: bool, received_ns: int) -> dict | None:
        if not isinstance(record, MarketDataRecord) or not authenticated or received_ns <= 0:
            return None
        price = record.ltp or record.close
        if not isinstance(price, (int, float)) or isinstance(price, bool) or not isfinite(price) or price <= 0:
            return None
        identity = f"{record.instrument.exchange.strip().upper()}:{record.instrument.segment.strip().upper()}:{record.instrument.token.strip()}"
        with self._lock:
            if not self._enabled or epoch != self._epoch or not self._authenticated or identity not in self._roles:
                return None
            role = self._roles[identity]
            volume_delta = None
            if record.volume is not None:
                previous = self._last_volume.get(identity)
                volume_delta = max(0, record.volume - previous) if previous is not None else None
                self._last_volume[identity] = record.volume
            self._history.setdefault(identity, deque(maxlen=MAX_HISTORY)).append(TimedQuote(
                instrument=identity, timestamp_ns=received_ns, price=float(price), bid=record.bid,
                ask=record.ask, bid_qty=record.bid_qty, ask_qty=record.ask_qty,
                volume_delta=volume_delta,
            ))
            self._evaluations += 1
            if received_ns - self._last_eval_ns < 100_000_000:
                return None
            self._last_eval_ns = received_ns
            required = tuple(self._roles.keys())
            sync = build_synchronized_snapshot(
                {key: tuple(items) for key, items in self._history.items()},
                now_ns=received_ns, max_age_ns=self.max_age_ns,
                max_skew_ns=self.max_skew_ns, required_instruments=required,
            )
            if not sync["aligned"] or any(len(self._history.get(key, ())) < 3 for key in required):
                self._last_result = {"status": "UNALIGNED_OR_INSUFFICIENT_HISTORY", "diagnostics": sync, "minimum_quotes_per_instrument": 3}
                return None
            self._aligned_evaluations += 1
            role_keys = {role: key for key, role in self._roles.items()}
            spot_key, future_key, cross_key = role_keys["spot"], role_keys["future"], role_keys["cross"]
            spot_quotes = list(self._history[spot_key])
            spot_prices = [q.price for q in spot_quotes]
            fut_prices = [q.price for q in self._history[future_key]]
            cross_prices = [q.price for q in self._history[cross_key]]
            last = spot_quotes[-1]
            prices = spot_prices[-MAX_HISTORY:]
            volumes = [q.volume_delta for q in spot_quotes[-MAX_HISTORY:]]
            # Exchange timestamps are never mixed with receive-monotonic time.
            age_seconds = (received_ns - last.timestamp_ns) / 1e9
            features = {
                "prices": prices, "volumes": volumes, "current_price": last.price,
                "bid": last.bid, "ask": last.ask, "bid_qty": last.bid_qty,
                "ask_qty": last.ask_qty, "quote_age_seconds": age_seconds,
                "feed_authenticated": True, "future_prices": fut_prices,
                "spot_prices": spot_prices, "cross_prices": cross_prices,
            }
            result = evaluate_market_features(features)
            result.update({"underlying": "NIFTY", "received_at_ns": received_ns,
                "feed_epoch": epoch, "sync": {"aligned": True,
                "observed_skew_ns": sync["observed_skew_ns"],
                "required_instruments": required, "role_map": dict(self._roles)}})
            self._last_result = result
            if result.get("eligible") and self.store.publish(result, now=received_ns / 1e9):
                self._signals += 1
            return result

    def snapshot(self) -> dict:
        with self._lock:
            return {"enabled": self._enabled, "configured_roles": dict(self._roles),
                "history_sizes": {k: len(v) for k, v in self._history.items()},
                "evaluations": self._evaluations, "aligned_evaluations": self._aligned_evaluations,
                "generated_signals": self._signals, "last_result": self._last_result,
                "scanner_only": True, "order_routing_enabled": False}
