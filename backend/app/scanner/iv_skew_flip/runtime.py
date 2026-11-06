"""Opt-in runtime wiring for the isolated IV Skew / IV Flip market-data path."""
from __future__ import annotations

from datetime import datetime
from threading import Event, RLock, Thread
from time import monotonic, time_ns
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .engine import (DEFAULT_CONFIGS, ScannerConfig, OptionQuote, Signal, evaluate_skew, evaluate_flip, evaluate_gamma_blast, confluence)
from .feed import IVSkewFlipFeedAdapter
from .sampler import IVOneSecondSampler
from .spot_feed import IVSpotFeedAdapter
from .universe import resolve_option_universe

IST = ZoneInfo("Asia/Kolkata")


class IVSkewFlipRuntime:
    """Coordinate independent spot/option consumers and one-second samplers.

    This service is opt-in: callers explicitly invoke start(). It never places
    orders, starts another broker socket, or borrows another strategy's tracker.
    """

    def __init__(
        self,
        *,
        instrument_master: Any,
        spot_feed: IVSpotFeedAdapter | None = None,
        option_feed_factory: Callable[[str], IVSkewFlipFeedAdapter] | None = None,
        clock_ns: Callable[[], int] = time_ns,
        clock_datetime: Callable[[], datetime] | None = None,
        refresh_seconds: float = 1.0,
        evaluation_seconds: float = 60.0,
    ) -> None:
        if refresh_seconds < 1.0 or refresh_seconds > 3.0:
            raise ValueError("universe refresh cadence must be between 1 and 3 seconds")
        self.instrument_master = instrument_master
        self.spot_feed = spot_feed or IVSpotFeedAdapter(clock_ns=clock_ns)
        self.option_feed_factory = option_feed_factory or (
            lambda symbol: IVSkewFlipFeedAdapter(consumer=f"iv-skew-flip-{symbol.lower()}", clock_ns=clock_ns)
        )
        self.clock_ns = clock_ns
        self.clock_datetime = clock_datetime or (lambda: datetime.now(IST))
        if evaluation_seconds != 60.0:
            raise ValueError("signal evaluation cadence is fixed at 60 seconds")
        self.refresh_seconds = refresh_seconds
        self.evaluation_seconds = evaluation_seconds
        self._lock = RLock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._rows_by_symbol: dict[str, tuple[dict[str, Any], ...]] = {}
        self._option_feeds: dict[str, IVSkewFlipFeedAdapter] = {}
        self._samplers: dict[str, IVOneSecondSampler] = {}
        self._universe_tokens: dict[str, tuple[str, ...]] = {}
        self._last_refresh_ns: int | None = None
        self._last_error: dict[str, str] = {}
        self._last_evaluation_ns: int | None = None
        self._previous_atm_iv_diff: dict[str, float] = {}
        self._signals: dict[str, dict[str, Any]] = {}
        self._started = False

    @staticmethod
    def _rows_for_symbol(instruments: list[dict[str, Any]], symbol: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            item for item in instruments
            if str(item.get("exch_seg", "")).strip().upper() == "NFO"
            and str(item.get("instrumenttype", "")).strip().upper() == "OPTIDX"
            and str(item.get("name", "")).strip().upper() == symbol
        )

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._started:
                return self.snapshot()
        instruments = self.instrument_master.download()
        if not instruments:
            raise RuntimeError("Angel One instrument master is empty")
        rows = {
            config.symbol: self._rows_for_symbol(instruments, config.symbol)
            for config in DEFAULT_CONFIGS
        }
        if any(not values for values in rows.values()):
            missing = sorted(symbol for symbol, values in rows.items() if not values)
            raise LookupError(f"instrument master has no NFO option contracts for {missing}")
        # Resolve both NSE index tokens before subscribing, so a partial/invalid
        # master cannot leave the runtime half-started.
        for symbol in ("NIFTY", "BANKNIFTY"):
            self.instrument_master.resolve_index_instrument(symbol, "NSE")
        self.spot_feed.start(self.instrument_master, ("NIFTY", "BANKNIFTY"))
        with self._lock:
            self._rows_by_symbol = rows
            self._started = True
            self._stop.clear()
            self._thread = Thread(
                target=self._run,
                name="iv-skew-flip-universe-refresh",
                daemon=True,
            )
            self._thread.start()
        return self.snapshot()

    def _refresh_symbol(self, config: ScannerConfig, spot: Any, now: datetime) -> None:
        symbol = config.symbol
        try:
            universe = resolve_option_universe(
                self._rows_by_symbol[symbol],
                underlying=symbol,
                expiry_type=config.expiry_type,
                spot=spot.price,
                today=now.astimezone(IST).date(),
                strike_count=7,
            )
            tokens = universe.tokens
            with self._lock:
                current_tokens = self._universe_tokens.get(symbol)
            if current_tokens == tokens:
                self._last_error.pop(symbol, None)
                return

            feed = self._option_feeds.get(symbol)
            if feed is None:
                feed = self.option_feed_factory(symbol)
            feed.start(universe)
            sampler = IVOneSecondSampler(
                quote_source=feed,
                spot_provider=lambda name=symbol: self.spot_feed.spot_snapshot(name),
                config=config,
                interval_seconds=1.0,
                clock_ns=self.clock_ns,
                clock_datetime=self.clock_datetime,
            )
            old_sampler = self._samplers.get(symbol)
            if old_sampler is not None:
                old_sampler.stop()
            sampler.start()
            with self._lock:
                self._option_feeds[symbol] = feed
                self._samplers[symbol] = sampler
                self._universe_tokens[symbol] = tokens
                self._last_error.pop(symbol, None)
        except Exception as exc:
            with self._lock:
                self._last_error[symbol] = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _signal_payload(signal: Signal) -> dict[str, Any]:
        return {
            "strategy": signal.strategy,
            "direction": signal.direction,
            "value": signal.value,
            "threshold": signal.threshold,
            "status": signal.status,
            "reason": signal.reason,
        }

    def _evaluate_symbol(self, config: ScannerConfig, now: datetime, now_ns: int) -> None:
        symbol = config.symbol
        feed = self._option_feeds.get(symbol)
        sampler = self._samplers.get(symbol)
        spot = self.spot_feed.spot_snapshot(symbol)
        if feed is None or sampler is None or spot is None or not hasattr(feed, "latest_quotes"):
            return
        quotes = tuple(feed.latest_quotes())
        if not quotes:
            return

        skew = evaluate_skew(quotes, spot.price, now, now_ns, config)
        flip = evaluate_flip(
            quotes, spot.price, now, now_ns, config,
            self._previous_atm_iv_diff.get(symbol),
        )
        if flip.value is not None and flip.status != "WAITING":
            self._previous_atm_iv_diff[symbol] = flip.value

        gamma_events: list[Signal] = []
        for quote in quotes:
            if (
                not quote.token
                or now_ns < quote.timestamp_ns
                or now_ns - quote.timestamp_ns > int(config.max_quote_age_seconds * 1_000_000_000)
            ):
                continue
            prior = sampler.oi_reference(quote.token, lookback_seconds=300)
            if prior is None:
                continue
            prior_quote = OptionQuote(
                underlying=prior.underlying, expiry=prior.expiry,
                strike=prior.strike, option_type=prior.option_type,
                timestamp_ns=prior.source_timestamp_ns, ltp=prior.mark,
                oi=prior.oi, token=prior.token,
            )
            gamma = evaluate_gamma_blast(
                quote, prior_quote,
                min_oi_drop_pct=config.gamma_min_oi_drop_pct,
                min_premium_rise_pct=config.gamma_min_premium_rise_pct,
            )
            if gamma.status == "SIGNAL":
                gamma_events.append(gamma)

        gamma_directions = {item.direction for item in gamma_events}
        if len(gamma_directions) == 1:
            direction = next(iter(gamma_directions))
            gamma_signal = Signal(
                "GAMMA_BLAST", direction, float(len(gamma_events)),
                float(config.gamma_min_oi_drop_pct), "SIGNAL",
                f"{len(gamma_events)} contracts confirm {direction} OI unwinding with premium expansion",
            )
        elif len(gamma_directions) > 1:
            gamma_signal = Signal(
                "GAMMA_BLAST", None, float(len(gamma_events)),
                float(config.gamma_min_oi_drop_pct), "NEUTRAL",
                "conflicting CE and PE unwinding signals; direction suppressed",
            )
        else:
            gamma_signal = Signal(
                "GAMMA_BLAST", None, 0.0,
                float(config.gamma_min_oi_drop_pct), "WAITING",
                "no confirmed five-minute OI reference with premium expansion",
            )

        combined = confluence((skew, flip, gamma_signal), required=2)
        with self._lock:
            self._signals[symbol] = {
                "evaluated_at_ns": now_ns,
                "evaluation_interval_seconds": self.evaluation_seconds,
                "strategies": [
                    self._signal_payload(skew),
                    self._signal_payload(flip),
                    self._signal_payload(gamma_signal),
                ],
                "confluence": self._signal_payload(combined),
                "signals_allowed": all(
                    item.status != "WAITING" for item in (skew, flip)
                ),
            }

    def _maybe_evaluate(self, now: datetime, now_ns: int) -> None:
        with self._lock:
            previous = self._last_evaluation_ns
        if previous is not None and now_ns - previous < int(self.evaluation_seconds * 1_000_000_000):
            return
        for config in DEFAULT_CONFIGS:
            self._evaluate_symbol(config, now, now_ns)
        with self._lock:
            self._last_evaluation_ns = now_ns

    def refresh_once(self) -> dict[str, Any]:
        now = self.clock_datetime()
        if now.tzinfo is None:
            raise ValueError("runtime datetime must be timezone-aware")
        for config in DEFAULT_CONFIGS:
            spot = self.spot_feed.spot_snapshot(config.symbol)
            if spot is None:
                continue
            self._refresh_symbol(config, spot, now)
        now_ns = self.clock_ns()
        self._maybe_evaluate(now, now_ns)
        with self._lock:
            self._last_refresh_ns = now_ns
        return self.snapshot()

    def _run(self) -> None:
        next_run = monotonic()
        while not self._stop.is_set():
            self.refresh_once()
            next_run += self.refresh_seconds
            delay = max(0.0, next_run - monotonic())
            if self._stop.wait(delay):
                break
            if delay == 0.0 and monotonic() - next_run > self.refresh_seconds:
                next_run = monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            started = self._started
            feeds = dict(self._option_feeds)
            samplers = dict(self._samplers)
            errors = dict(self._last_error)
            signals = dict(self._signals)
            last_refresh = self._last_refresh_ns
            last_evaluation = self._last_evaluation_ns
        return {
            "status": "RUNNING" if started else "STOPPED",
            "consumer": "iv-skew-flip",
            "orders_enabled": False,
            "refresh_seconds": self.refresh_seconds,
            "signal_evaluation_seconds": self.evaluation_seconds,
            "last_evaluation_ns": last_evaluation,
            "signals": signals,
            "spot_feed": self.spot_feed.snapshot(),
            "indices": {
                symbol: {
                    "option_feed": feeds[symbol].snapshot() if symbol in feeds else {"status": "WAITING_FOR_SPOT"},
                    "sampler": samplers[symbol].snapshot() if symbol in samplers else {"status": "WAITING"},
                    "universe_tokens": len(self._universe_tokens.get(symbol, ())),
                    "error": errors.get(symbol),
                    "signals": signals.get(symbol),
                }
                for symbol in ("NIFTY", "BANKNIFTY")
            },
            "last_refresh_ns": last_refresh,
        }

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        with self._lock:
            samplers = tuple(self._samplers.values())
            feeds = tuple(self._option_feeds.values())
        for sampler in samplers:
            sampler.stop()
        for feed in feeds:
            feed.stop()
        self.spot_feed.stop()
        with self._lock:
            self._started = False
            self._thread = None
            self._samplers.clear()
            self._option_feeds.clear()
            self._universe_tokens.clear()
            self._rows_by_symbol.clear()
            self._signals.clear()
            self._previous_atm_iv_diff.clear()
            self._last_evaluation_ns = None
        return self.snapshot()


__all__ = ["IVSkewFlipRuntime"]
