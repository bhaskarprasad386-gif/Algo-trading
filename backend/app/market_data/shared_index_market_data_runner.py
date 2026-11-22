"""Isolated live-data runner for the locked NIFTY/BANKNIFTY market universe.

Starts only spot + option subscriptions on the existing shared Angel One feed.
It publishes latest tick state for future strategy consumers, but does not
create strategy signals, orders, paper trades, or alter application startup.
"""
from __future__ import annotations

from datetime import date, datetime
from threading import RLock
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from .instruments import InstrumentMaster
from .shared_index_feed_lifecycle import SharedIndexFeedLifecycle
from .shared_index_market_data_state import SharedIndexMarketDataState
from .shared_index_option_rotation_controller import SharedIndexOptionRotationController
from .shared_index_spot_resolver import resolve_locked_index_spot_descriptors
from .shared_index_option_selection import _parse_expiry


IST = ZoneInfo("Asia/Kolkata")
LOCKED = ("NIFTY", "BANKNIFTY")


def _expiry_candidates(rows: Iterable[dict], underlying: str) -> list[date]:
    dates = set()
    for row in rows:
        if (str(row.get("exch_seg", "")).strip().upper() != "NFO"
                or str(row.get("name", "")).strip().upper() != underlying
                or str(row.get("instrumenttype", "")).strip().upper() != "OPTIDX"):
            continue
        try:
            dates.add(_parse_expiry(row.get("expiry")))
        except (ValueError, TypeError):
            continue
    return sorted(dates)


def choose_locked_expiry(rows: Iterable[dict], underlying: str, *, today: date | None = None) -> str:
    """Choose next listed NIFTY weekly or next listed BANKNIFTY monthly expiry."""
    symbol = str(underlying).strip().upper()
    if symbol not in LOCKED:
        raise ValueError("expiry selection is restricted to NIFTY/BANKNIFTY")
    current = today or datetime.now(IST).date()
    dates = [d for d in _expiry_candidates(rows, symbol) if d >= current]
    if not dates:
        raise LookupError(f"no future option expiries listed for {symbol}")
    if symbol == "NIFTY":
        return min(dates).isoformat()
    # Monthly expiries are the last listed expiry date in each calendar month.
    month_last = {}
    for expiry in dates:
        month_last[(expiry.year, expiry.month)] = max(
            month_last.get((expiry.year, expiry.month), expiry), expiry
        )
    monthly = sorted(d for d in month_last.values() if d >= current)
    if not monthly:
        raise LookupError("no future BANKNIFTY monthly expiry in listed master")
    return monthly[0].isoformat()


class SharedIndexMarketDataRunner:
    """Explicit ON/OFF lifecycle; isolated from application startup and strategy logic."""

    def __init__(self, *, instrument_master: Any | None = None,
                 publish: Callable[[Any], None] | None = None,
                 rotation_thresholds: dict[str, float] | None = None,
                 cooldown_seconds: float = 1.0,
                 clock=None):
        self.instrument_master = instrument_master or InstrumentMaster()
        self.state = SharedIndexMarketDataState(publish=publish)
        self.lifecycle = SharedIndexFeedLifecycle(record_callback=self._on_record)
        thresholds = rotation_thresholds or {"NIFTY": 20.0, "BANKNIFTY": 50.0}
        controller_kwargs = {
            "lifecycle": self.lifecycle,
            "instrument_rows": lambda: tuple(self._rows),
            "expiry_for": self._expiry_for,
            "thresholds": thresholds,
            "cooldown_seconds": cooldown_seconds,
        }
        if clock is not None:
            controller_kwargs["clock"] = clock
        self.rotation = SharedIndexOptionRotationController(**controller_kwargs)
        self._lock = RLock()
        self._rows: tuple[dict, ...] = ()
        self._running = False
        self._start_error: str | None = None
        self._started_at: str | None = None
        self._bootstrap_options_done = False

    def _expiry_for(self, underlying: str) -> str:
        return choose_locked_expiry(self._rows, underlying)

    def _bootstrap_options(self, record) -> bool:
        # First usable spot tick bootstraps the initial ATM window immediately;
        # later ticks are handled by the movement-based rotation controller.
        with self._lock:
            if self._bootstrap_options_done:
                return False
            self._bootstrap_options_done = True
            rows = self._rows
        try:
            from .common_strategy_feed import CommonStrategyMarketFeed
            descriptors = []
            for underlying in LOCKED:
                expiry = choose_locked_expiry(rows, underlying)
                selected = __import__(
                    "app.market_data.shared_index_option_selection",
                    fromlist=["select_locked_index_options"],
            ).select_locked_index_options(rows, underlying=underlying,
                                         expiry=expiry, spot_price=float(record.ltp))
                descriptors.extend(CommonStrategyMarketFeed.descriptor(
                    exchange="NFO", token=contract.token, symbol=contract.trading_symbol,
                    instrument_type="option", segment="NFO", expiry=contract.expiry,
                    strike=contract.strike, option_type=contract.option_type,
                    lot_size=contract.lot_size, underlying=contract.underlying,
                ) for contract in selected.contracts)
            self.lifecycle.start_options(tuple(descriptors))
            with self._lock:
                self._start_error = None
            return True
        except Exception as exc:
            with self._lock:
                self._start_error = f"{type(exc).__name__}: {exc}"[:240]
            # Startup selection is retryable on the next live spot update.
            with self._lock:
                self._bootstrap_options_done = False
            return False

    def _on_record(self, record) -> None:
        if not self.state.on_record(record):
            return
        if getattr(record, "instrument_type", None) is not None and getattr(record.instrument_type, "value", "") == "index":
            with self._lock:
                running = self._running
            if running:
                self._bootstrap_options(record)
                self.rotation.on_record(record)

    def start(self) -> dict:
        with self._lock:
            if self._running:
                return self.snapshot()
            self._start_error = None
        try:
            rows = tuple(self.instrument_master.download())
            if not rows:
                raise LookupError("Angel One instrument master is empty")
            spots = resolve_locked_index_spot_descriptors(self.instrument_master)
            with self._lock:
                self._rows = rows
            self.lifecycle.start_spot(spots)
            with self._lock:
                self._running = True
                self._started_at = datetime.now(IST).isoformat()
            return self.snapshot()
        except Exception as exc:
            with self._lock:
                self._start_error = f"{type(exc).__name__}: {exc}"[:240]
            try:
                self.lifecycle.stop()
            except Exception:
                pass
            return self.snapshot()

    def stop(self) -> dict:
        with self._lock:
            was_running = self._running
            self._running = False
            self._bootstrap_options_done = False
        try:
            self.lifecycle.stop()
        finally:
            with self._lock:
                self._started_at = None
        return self.snapshot()

    def flush_rotations(self) -> tuple[str, ...]:
        with self._lock:
            if not self._running:
                return ()
        return self.rotation.flush_due()

    def latest(self, instrument_key: str):
        return self.state.latest(instrument_key)

    def snapshot(self) -> dict:
        with self._lock:
            running = self._running
            started_at = self._started_at
            error = self._start_error
            bootstrap_done = self._bootstrap_options_done
        state = self.state.snapshot()
        lifecycle = self.lifecycle.snapshot()
        rotation = self.rotation.snapshot()
        return {
            "runner": "shared-index-market-data",
            "enabled": running,
            "started_at": started_at,
            "last_error": error,
            "initial_option_bootstrap_done": bootstrap_done,
            "spot_universe": list(LOCKED),
            "option_universe": {"NIFTY": "weekly ATM +/- 6", "BANKNIFTY": "monthly ATM +/- 5"},
            "state": state,
            "lifecycle": lifecycle,
            "rotation": rotation,
            "live_data_ready": running and state["fresh_count"] > 0,
        }


__all__ = ["SharedIndexMarketDataRunner", "choose_locked_expiry"]
