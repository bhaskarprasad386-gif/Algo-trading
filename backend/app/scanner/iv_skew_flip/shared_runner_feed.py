"""Read-only IV adapters over the application's live shared index runner.

These adapters never register consumers or change subscriptions. IV receives the
already-published NIFTY Weekly / BANKNIFTY Monthly quote state and performs its
sampling, persistence and signal calculations on IV-owned workers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from time import time_ns
from typing import Any, Callable

from .engine import OptionQuote
from .sampler import SpotSnapshot


class IVSharedRunnerSpotFeedAdapter:
    """Read NIFTY/BANKNIFTY spot ticks from SharedIndexMarketDataRunner state."""

    DEFAULT_SYMBOLS = ("NIFTY", "BANKNIFTY")

    def __init__(
        self,
        *,
        shared_runner: Any,
        clock_ns: Callable[[], int] = time_ns,
        max_spot_age_seconds: float = 5.0,
    ) -> None:
        if shared_runner is None:
            raise ValueError("shared_runner is required")
        if max_spot_age_seconds <= 0:
            raise ValueError("max_spot_age_seconds must be positive")
        self.shared_runner = shared_runner
        self.clock_ns = clock_ns
        self.max_age_ns = int(max_spot_age_seconds * 1_000_000_000)
        self._lock = RLock()
        self._last: dict[str, SpotSnapshot] = {}
        self._started = False

    def start(self, _instrument_master: Any = None, symbols=("NIFTY", "BANKNIFTY")) -> dict[str, Any]:
        selected = {str(item).strip().upper() for item in symbols}
        if selected != set(self.DEFAULT_SYMBOLS):
            raise ValueError("IV shared spot adapter is locked to NIFTY and BANKNIFTY")
        with self._lock:
            self._started = True
        self._refresh_last()
        return {
            "status": "ATTACHED",
            "source": "shared-index-market-data",
            "symbols": list(self.DEFAULT_SYMBOLS),
            "owns_subscriptions": False,
        }

    def _records(self) -> list[dict[str, Any]]:
        state = getattr(self.shared_runner, "state", None)
        records = getattr(state, "records_snapshot", None)
        return records() if callable(records) else []

    def _refresh_last(self) -> None:
        updates: dict[str, SpotSnapshot] = {}
        for row in self._records():
            if str(row.get("instrument_type") or "").lower() != "index":
                continue
            symbol = str(row.get("underlying") or "").strip().upper()
            timestamp = row.get("timestamp_ns")
            price = row.get("ltp")
            if symbol not in self.DEFAULT_SYMBOLS:
                continue
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
                continue
            if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
                continue
            updates[symbol] = SpotSnapshot(float(price), timestamp, timestamp)
        if updates:
            with self._lock:
                self._last.update(updates)

    def last_spot_snapshot(self, symbol: str) -> SpotSnapshot | None:
        self._refresh_last()
        with self._lock:
            return self._last.get(str(symbol).strip().upper())

    def spot_snapshot(self, symbol: str, *, now_ns: int | None = None) -> SpotSnapshot | None:
        quote = self.last_spot_snapshot(symbol)
        if quote is None:
            return None
        now = self.clock_ns() if now_ns is None else now_ns
        age = now - quote.timestamp_ns
        if age < -1_000_000_000 or age > self.max_age_ns:
            return None
        return quote

    def snapshot(self, *, transport_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        self._refresh_last()
        now = self.clock_ns()
        with self._lock:
            latest = dict(self._last)
            started = self._started
        spot: dict[str, Any] = {}
        fresh_count = 0
        for symbol in self.DEFAULT_SYMBOLS:
            quote = latest.get(symbol)
            age_ns = None if quote is None else now - quote.timestamp_ns
            fresh = quote is not None and age_ns is not None and -1_000_000_000 <= age_ns <= self.max_age_ns
            if fresh:
                fresh_count += 1
            spot[symbol] = {
                "price": None if quote is None else quote.price,
                "last_price": None if quote is None else quote.price,
                "timestamp_ns": None if quote is None else quote.timestamp_ns,
                "last_trade_time": (
                    datetime.fromtimestamp(quote.timestamp_ns / 1_000_000_000, tz=timezone.utc).isoformat()
                    if quote is not None else None
                ),
                "age_ms": None if age_ns is None else round(max(0, age_ns) / 1_000_000, 2),
                "source_age_ms": None if age_ns is None else round(max(0, age_ns) / 1_000_000, 2),
                "fresh": fresh,
                "quote_status": "LIVE" if fresh else "LAST_KNOWN_STALE" if quote is not None else "WAITING_FOR_TICK",
            }
        status = "STOPPED" if not started else "LIVE" if fresh_count == 2 else "PARTIAL" if fresh_count else "STALE" if any(latest.values()) else "WAITING_FOR_TICKS"
        return {
            "status": status,
            "source": "shared-index-market-data",
            "consumer": "iv-skew-flip-spot",
            "signals_allowed": fresh_count == 2,
            "spot": spot,
            "transport": transport_snapshot,
            "subscription_owner": "shared-index-market-data",
            "owns_subscriptions": False,
        }

    def stop(self) -> None:
        # IV OFF only detaches this reader; it never stops the shared runner.
        with self._lock:
            self._started = False


class IVSharedRunnerOptionFeedAdapter:
    """Read one bounded IV expiry window from the shared runner's published ticks."""

    def __init__(
        self,
        *,
        shared_runner: Any,
        stream_id: str,
        clock_ns: Callable[[], int] = time_ns,
        max_quote_age_seconds: float = 5.0,
    ) -> None:
        if shared_runner is None:
            raise ValueError("shared_runner is required")
        self.shared_runner = shared_runner
        self.stream_id = str(stream_id).strip().upper()
        self.clock_ns = clock_ns
        self.max_age_ns = int(max_quote_age_seconds * 1_000_000_000)
        self._lock = RLock()
        self._universe = None
        self._started = False

    def _active_descriptors(self) -> tuple[Any, ...]:
        lifecycle = getattr(self.shared_runner, "lifecycle", None)
        getter = getattr(lifecycle, "option_descriptors", None)
        return tuple(getter()) if callable(getter) else ()

    def start(self, universe: Any) -> dict[str, Any]:
        if universe is None or not getattr(universe, "contracts", None):
            raise ValueError("a resolved IV option universe is required")
        health = getattr(self.shared_runner, "health_snapshot", None)
        runner_status = health() if callable(health) else self.shared_runner.snapshot()
        if not runner_status.get("enabled") or not runner_status.get("initial_option_bootstrap_done"):
            raise RuntimeError("SHARED_RUNNER_NOT_READY: shared index options are not live")
        descriptors = self._active_descriptors()
        active = {
            str(item.key.token): item
            for item in descriptors
            if str(getattr(item, "underlying", "")).upper() == universe.underlying
        }
        requested = {str(item.token) for item in universe.contracts}
        missing = requested - set(active)
        if missing:
            raise RuntimeError(
                f"SHARED_RUNNER_UNIVERSE_MISMATCH: {universe.underlying} {universe.expiry}; "
                f"{len(missing)} IV contracts are not in the shared runner's active window"
            )
        mismatched = [
            token for token in requested
            if str(getattr(active[token], "expiry", ""))[:10] != str(universe.expiry)[:10]
        ]
        if mismatched:
            raise RuntimeError(
                f"SHARED_RUNNER_EXPIRY_MISMATCH: expected {universe.expiry}, "
                "shared runner has a different active expiry"
            )
        with self._lock:
            self._universe = universe
            self._started = True
        return {
            "status": "ATTACHED",
            "consumer": f"iv-skew-flip-{universe.underlying.lower()}",
            "source": "shared-index-market-data",
            "underlying": universe.underlying,
            "expiry": universe.expiry,
            "contract_count": len(requested),
            "shared_underlying_contract_count": len(active),
            "owns_subscriptions": False,
        }

    def resolved_universe(self):
        with self._lock:
            return self._universe

    def latest_quotes(self) -> tuple[OptionQuote, ...]:
        with self._lock:
            universe = self._universe
            started = self._started
        if not started or universe is None:
            return ()
        requested = {str(item.token) for item in universe.contracts}
        result = []
        for row in self.shared_runner.state.records_snapshot():
            token = str(row.get("token") or "")
            if token not in requested or str(row.get("instrument_type") or "").lower() != "option":
                continue
            if str(row.get("underlying") or "").upper() != universe.underlying:
                continue
            if str(row.get("expiry") or "")[:10] != universe.expiry[:10]:
                continue
            timestamp = row.get("timestamp_ns")
            strike = row.get("strike")
            side = str(row.get("option_type") or "").upper()
            if (
                isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0
                or isinstance(strike, bool) or not isinstance(strike, (int, float))
                or side not in {"CE", "PE"}
            ):
                continue
            result.append(OptionQuote(
                underlying=universe.underlying,
                expiry=universe.expiry,
                strike=float(strike),
                option_type=side,
                timestamp_ns=timestamp,
                ltp=row.get("ltp"),
                bid=row.get("bid"),
                ask=row.get("ask"),
                oi=row.get("oi"),
                volume=row.get("volume"),
                token=token,
                bid_qty=row.get("bid_qty"),
                ask_qty=row.get("ask_qty"),
            ))
        return tuple(result)

    def snapshot(self, now_ns: int | None = None) -> dict[str, Any]:
        now = self.clock_ns() if now_ns is None else now_ns
        with self._lock:
            universe = self._universe
            started = self._started
        if universe is None:
            return {"status": "STOPPED" if not started else "WAITING_FOR_SHARED_RUNNER", "source": "shared-index-market-data"}
        requested = {str(item.token) for item in universe.contracts}
        rows = [
            row for row in self.shared_runner.state.records_snapshot()
            if str(row.get("token") or "") in requested
            and str(row.get("underlying") or "").upper() == universe.underlying
            and str(row.get("expiry") or "")[:10] == universe.expiry[:10]
        ]
        fresh = sum(
            1 for row in rows
            if isinstance(row.get("timestamp_ns"), int)
            and -1_000_000_000 <= now - row["timestamp_ns"] <= self.max_age_ns
        )
        expected = len(requested)
        status = "STOPPED" if not started else "LIVE" if fresh == expected else "PARTIAL" if fresh else "WAITING_FOR_TICKS" if not rows else "STALE"
        return {
            "status": status,
            "source": "shared-index-market-data",
            "underlying": universe.underlying,
            "expiry": universe.expiry,
            "expected_contracts": expected,
            "fresh_contracts": fresh,
            "missing_contracts": max(0, expected - fresh),
            "atm_strike": universe.atm_strike,
            "strikes": list(universe.strikes),
            "tokens": sorted(requested),
            "owns_subscriptions": False,
        }

    def stop(self) -> None:
        # IV OFF only detaches its reader; shared option subscriptions stay live.
        with self._lock:
            self._started = False
            self._universe = None


__all__ = ["IVSharedRunnerSpotFeedAdapter", "IVSharedRunnerOptionFeedAdapter"]
