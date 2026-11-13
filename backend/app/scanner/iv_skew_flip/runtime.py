"""Opt-in runtime wiring for the isolated IV Skew / IV Flip market-data path."""
from __future__ import annotations

from datetime import datetime, time as datetime_time, timedelta
import json
from threading import Event, RLock, Thread
from time import monotonic, time_ns
from typing import Any, Callable
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from html import escape
import math

from app.core.config import settings
from .alerts.notifications import (
    AmbiguousDeliveryError, TelegramRateLimitError, TelegramRejectedError,
    TelegramOutboxWorker,
)
from zoneinfo import ZoneInfo

from app.core.database import SessionLocal
from app.models.iv_signal_event import IVSignalEvent

from .engine import (DEFAULT_CONFIGS, ScannerConfig, OptionQuote, Signal, evaluate_skew, evaluate_flip, evaluate_gamma_blast, confluence)
from .early_move import IVEarlyMoveDetector
from .basis import BasisHistory, calculate_basis, resolve_index_future_contracts
from .future_feed import IVIndexFutureFeedAdapter
from .equity_feed import IVWeightedEquityFeedAdapter, WeightedEquity
from .equity_pressure import ComponentSample, ComponentWeight, EquityComponentPressure
from .feed import IVSkewFlipFeedAdapter
from .instruments import download_iv_instruments
from .sampler import IVOneSecondSampler
from .spot_feed import IVSpotFeedAdapter
from .alerts import AlertCooldownStore, IVEarlyEntryAlertEngine
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from .universe import resolve_option_universe

IST = ZoneInfo("Asia/Kolkata")


def _universe_selection_date(now: datetime):
    """Roll the selected option universe after the 15:30 IST expiry close."""
    local_now = now.astimezone(IST)
    selected_date = local_now.date()
    if local_now.time() >= datetime_time(15, 30):
        selected_date += timedelta(days=1)
    return selected_date


# IV scanner is intentionally running only NIFTY Weekly until explicitly enabled.
# Keep the complete registry for stable API/UI reporting, but all runtime work
# (subscriptions, sampling, signals and refresh) uses ACTIVE_CHAIN_CONFIGS only.
ALL_CHAIN_CONFIGS = (
    ("NIFTY_WEEKLY", DEFAULT_CONFIGS[0]),
    ("NIFTY_MONTHLY", ScannerConfig("NIFTY", "MONTHLY", 0.8, -0.8)),
    ("BANKNIFTY_MONTHLY", DEFAULT_CONFIGS[1]),
)
ACTIVE_CHAIN_CONFIGS = (ALL_CHAIN_CONFIGS[0],)
CHAIN_CONFIGS = ALL_CHAIN_CONFIGS


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
        equity_pressure_enabled: bool = False,
        equity_basket: tuple[WeightedEquity, ...] | None = None,
        equity_baskets: dict[str, tuple[WeightedEquity, ...]] | None = None,
        equity_feed_factory: Callable[[], IVWeightedEquityFeedAdapter] | None = None,
        equity_pressure_unavailable_reason: str | None = None,
        future_feed: IVIndexFutureFeedAdapter | None = None,
        feed_manager: Any | None = None,
    ) -> None:
        if refresh_seconds < 1.0 or refresh_seconds > 3.0:
            raise ValueError("universe refresh cadence must be between 1 and 3 seconds")
        self.instrument_master = instrument_master
        self.feed_manager = feed_manager
        # Production injects a dedicated manager. All IV consumers use mode 3
        # on that one manager so spot, equity and option quotes share exactly
        # one IV-owned broker socket without joining the strategy shared feed.
        self.spot_feed = spot_feed or IVSpotFeedAdapter(
            feed=CommonStrategyMarketFeed(
                "iv-skew-flip-spot", manager=feed_manager, mode=3,
            ) if feed_manager is not None else None,
            clock_ns=clock_ns,
            # Preserve the durable last-known spot display when using an
            # injected dedicated feed adapter.
            persist_last_price=True if feed_manager is not None else None,
        )
        self.future_feed = future_feed or IVIndexFutureFeedAdapter(
            feed=CommonStrategyMarketFeed(
                "iv-skew-flip-index-futures", manager=feed_manager, mode=3,
            ) if feed_manager is not None else None,
            clock_ns=clock_ns,
        )
        self._future_contracts: tuple[Any, ...] = ()
        self._basis_histories: dict[str, BasisHistory] = {}
        self._basis_analysis: dict[str, dict[str, Any]] = {}
        self.option_feed_factory = option_feed_factory or (
            lambda symbol: IVSkewFlipFeedAdapter(
                feed=CommonStrategyMarketFeed(
                    f"iv-skew-flip-{symbol.lower()}", manager=feed_manager, mode=3,
                ) if feed_manager is not None else None,
                consumer=f"iv-skew-flip-{symbol.lower()}",
                clock_ns=clock_ns,
            )
        )
        self.clock_ns = clock_ns
        self.clock_datetime = clock_datetime or (lambda: datetime.now(IST))
        if evaluation_seconds != 60.0:
            raise ValueError("signal evaluation cadence is fixed at 60 seconds")
        self.refresh_seconds = refresh_seconds
        self.evaluation_seconds = evaluation_seconds
        if not isinstance(equity_pressure_enabled, bool):
            raise ValueError("equity_pressure_enabled must be boolean")
        self.equity_pressure_enabled = equity_pressure_enabled
        self.equity_pressure_unavailable_reason = (
            str(equity_pressure_unavailable_reason).strip()
            if equity_pressure_unavailable_reason else None
        )
        self.equity_basket = tuple(equity_basket or ())
        self.equity_baskets = {
            str(symbol).strip().upper(): tuple(items)
            for symbol, items in (equity_baskets or {}).items()
        }
        if self.equity_baskets and set(self.equity_baskets) - {"NIFTY", "BANKNIFTY"}:
            raise ValueError("equity pressure supports only NIFTY and BANKNIFTY baskets")
        if any(not items for items in self.equity_baskets.values()):
            raise ValueError("each index requires a non-empty verified weight basket")
        if self.equity_pressure_enabled and not self.equity_basket and not self.equity_baskets:
            raise ValueError("equity pressure requires an injected verified weight basket")
        self.equity_feed_factory = equity_feed_factory or (
            lambda: IVWeightedEquityFeedAdapter(
                feed=CommonStrategyMarketFeed(
                    "iv-skew-flip-equities", manager=feed_manager, mode=3,
                ) if feed_manager is not None else None,
                clock_ns=clock_ns,
            )
        )
        self._equity_feed: IVWeightedEquityFeedAdapter | None = None
        self._equity_pressures: dict[str, EquityComponentPressure] = {
            symbol: EquityComponentPressure(
                tuple(ComponentWeight(item.symbol, item.weight) for item in items)
            )
            for symbol, items in self.equity_baskets.items()
        }
        self._equity_pressure_by_underlying: dict[str, dict[str, Any]] = {}
        self._equity_pressure = (
            EquityComponentPressure(
                tuple(ComponentWeight(item.symbol, item.weight) for item in self.equity_basket)
            )
            if self.equity_pressure_enabled and self.equity_basket else None
        )
        self._equity_pressure_last_snapshot: dict[str, Any] = {
            "status": "UNAVAILABLE" if self.equity_pressure_unavailable_reason else "DISABLED",
            "reason": self.equity_pressure_unavailable_reason,
            "signals_allowed": False, "direction": "WAITING",
            "timeframe_agreement": False, "windows": {},
        }
        self._lock = RLock()
        # Serialize full startup/shutdown transactions, including slow external
        # feed registration and instrument-master loading.
        self._lifecycle_lock = RLock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._telegram_thread: Thread | None = None
        self._rows_by_stream: dict[str, tuple[dict[str, Any], ...]] = {}
        self._option_feeds: dict[str, IVSkewFlipFeedAdapter] = {}
        self._samplers: dict[str, IVOneSecondSampler] = {}
        self._universe_tokens: dict[str, tuple[str, ...]] = {}
        self._expiry_diagnostics: dict[str, dict[str, Any]] = {}
        self._last_refresh_ns: int | None = None
        self._last_error: dict[str, str] = {}
        self._last_evaluation_ns: int | None = None
        self._previous_atm_iv_diff: dict[str, float] = {}
        self._previous_atm_strike: dict[str, float] = {}
        self._signals: dict[str, dict[str, Any]] = {}
        self._early_move_detectors = {stream_id: IVEarlyMoveDetector() for stream_id, _ in ACTIVE_CHAIN_CONFIGS}
        self._early_move: dict[str, dict[str, Any]] = {}
        self._early_entry_alerts = IVEarlyEntryAlertEngine(AlertCooldownStore(SessionLocal, cooldown_seconds=600))
        self._telegram_outbox = TelegramOutboxWorker(SessionLocal)
        self._telegram_delivery = {"status": "DISABLED", "last_attempt_ns": None, "last_error": None}
        self._started = False
        self._stopping = False

    @staticmethod
    def _rows_for_symbol(instruments: list[dict[str, Any]], symbol: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            item for item in instruments
            if str(item.get("exch_seg", "")).strip().upper() == "NFO"
            and str(item.get("instrumenttype", "")).strip().upper() == "OPTIDX"
            and str(item.get("name", "")).strip().upper() == symbol
        )

    def start(self, *, instruments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        # A separate re-entrant lock covers the entire lifecycle transaction.
        # Re-entrancy is required because failed startup calls stop() to unwind.
        with self._lifecycle_lock:
            return self._start_locked(instruments=instruments)

    def _start_locked(self, *, instruments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._stopping:
                raise RuntimeError("IV scanner runtime is still stopping; retry after shutdown completes")
            if self._started:
                return self.snapshot()
        if instruments is None:
            instruments = download_iv_instruments(self.instrument_master)
        if not instruments:
            raise RuntimeError("Angel One instrument master is empty")
        rows = {
            stream_id: self._rows_for_symbol(instruments, config.symbol)
            for stream_id, config in ACTIVE_CHAIN_CONFIGS
        }
        if any(not values for values in rows.values()):
            missing = sorted(stream_id for stream_id, values in rows.items() if not values)
            raise LookupError(f"instrument master has no NFO option contracts for streams {missing}")
        # Resolve the sole enabled NSE index token before subscribing, so an
        # invalid master cannot leave the runtime half-started.
        for symbol in ("NIFTY",):
            self.instrument_master.resolve_index_instrument(symbol, "NSE")
        # Index-future basis feed is intentionally disabled in NIFTY-Weekly-only mode.
        future_contracts: tuple[Any, ...] = ()
        if self.equity_pressure_enabled and self.equity_baskets:
            # One physical quote consumer for the union of both top-10 baskets.
            # Shared constituents are resolved/subscribed once; each index still
            # applies its own official weight in its independent pressure engine.
            unique: dict[str, WeightedEquity] = {}
            for basket in self.equity_baskets.values():
                for item in basket:
                    unique.setdefault(item.symbol.strip().upper(), item)
            equity_feed = self.equity_feed_factory()
            # Keep ownership before start(): a shared-feed adapter can register
            # its consumer and then raise while subscribing.
            self._equity_feed = equity_feed
            try:
                equity_feed.start(self.instrument_master, tuple(unique.values()))
            except Exception as exc:
                cleanup = self.stop()
                if cleanup.get("status") != "STOPPED":
                    raise RuntimeError(
                        "IV equity-feed startup failed and cleanup is pending; retry stop before starting again"
                    ) from exc
                raise
        elif self.equity_pressure_enabled:
            equity_feed = self.equity_feed_factory()
            self._equity_feed = equity_feed
            try:
                equity_feed.start(self.instrument_master, self.equity_basket)
            except Exception as exc:
                cleanup = self.stop()
                if cleanup.get("status") != "STOPPED":
                    raise RuntimeError(
                        "IV equity-feed startup failed and cleanup is pending; retry stop before starting again"
                    ) from exc
                raise
        try:
            self.spot_feed.start(self.instrument_master, ("NIFTY",))
            if future_contracts:
                self.future_feed.start(future_contracts)
        except Exception as exc:
            cleanup = self.stop()
            if cleanup.get("status") != "STOPPED":
                raise RuntimeError(
                    "IV spot/future-feed startup failed and cleanup is pending; retry stop before starting again"
                ) from exc
            raise
        with self._lock:
            self._rows_by_stream = rows
            self._future_contracts = future_contracts
            self._basis_histories = {f"{item.underlying}:{item.expiry.isoformat()}": BasisHistory() for item in future_contracts}
            self._basis_analysis = {}
            self._started = True
            self._stop.clear()
            self._thread = Thread(
                target=self._run,
                name="iv-skew-flip-universe-refresh",
                daemon=True,
            )
            self._thread.start()
            self._telegram_thread = Thread(
                target=self._run_telegram_notifications,
                name="iv-skew-flip-telegram-outbox",
                daemon=True,
            )
            self._telegram_thread.start()
        return self.snapshot()

    @staticmethod
    def _future_expiry_candidates(rows: tuple[dict[str, Any], ...], today: Any) -> list[str]:
        candidates: set[str] = set()
        for row in rows:
            raw = str(row.get("expiry", "")).strip()
            parsed = None
            for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
                try:
                    parsed = datetime.strptime(raw.upper(), fmt).date()
                    break
                except ValueError:
                    continue
            if parsed is not None and parsed >= today:
                candidates.add(parsed.isoformat())
        return sorted(candidates)

    def _refresh_stream(self, stream_id: str, config: ScannerConfig, spot: Any, now: datetime) -> None:
        symbol = stream_id
        try:
            # Index options stop trading at 15:30 IST on expiry day. Move
            # the selection date forward at that boundary so the next refresh
            # selects the next listed weekly expiry and keeps it on the next day.
            universe_date = _universe_selection_date(now)
            universe = resolve_option_universe(
                self._rows_by_stream[symbol],
                underlying=config.symbol,
                expiry_type=config.expiry_type,
                spot=spot.price,
                today=universe_date,
                strike_count=11 if config.symbol == "NIFTY" and config.expiry_type == "WEEKLY" else 7,
            )
            tokens = universe.tokens
            with self._lock:
                self._expiry_diagnostics[symbol] = {
                    "selection_date": universe_date.isoformat(),
                    "available_expiries": self._future_expiry_candidates(
                        self._rows_by_stream[symbol], universe_date,
                    ),
                    "selected_expiry": universe.expiry,
                }
                current_tokens = self._universe_tokens.get(symbol)
                feed = self._option_feeds.get(symbol)
                sampler = self._samplers.get(symbol)

            # A successful subscription is committed independently from sampler
            # startup. If sampler startup fails, the next refresh retries only
            # the sampler; it must not clear/re-register an unchanged consumer.
            if current_tokens != tokens or feed is None:
                if feed is None:
                    feed = self.option_feed_factory(symbol)
                # Own the adapter before start(): start may partially register
                # with the shared feed before raising.
                with self._lock:
                    self._option_feeds[symbol] = feed
                feed.start(universe)
                with self._lock:
                    self._universe_tokens[symbol] = tokens
                    self._previous_atm_iv_diff.pop(symbol, None)
                    self._previous_atm_strike.pop(symbol, None)
                sampler = None

            if sampler is None:
                sampler = IVOneSecondSampler(
                    quote_source=feed,
                    spot_provider=lambda name=config.symbol: self.spot_feed.spot_snapshot(name),
                    config=config,
                    interval_seconds=1.0,
                    clock_ns=self.clock_ns,
                    clock_datetime=self.clock_datetime,
                    history_session_factory=SessionLocal,
                )
                old_sampler = self._samplers.get(symbol)
                sampler.inherit_history(old_sampler, universe)
                # Retain sampler ownership before start(), just like feeds.
                with self._lock:
                    self._samplers[symbol] = sampler
                sampler.start()
                if old_sampler is not None and old_sampler is not sampler:
                    old_sampler.stop()
            else:
                # start() is idempotent and recovers a sampler whose worker
                # failed to start without touching the already-live option feed.
                sampler.start()

            with self._lock:
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

    def _mark_waiting(self, symbol: str, now_ns: int, reason: str) -> None:
        confluence_waiting = Signal("CONFLUENCE", None, 0.0, 2.0, "WAITING", reason)
        with self._lock:
            self._signals[symbol] = {
                "evaluated_at_ns": now_ns,
                "evaluation_interval_seconds": self.evaluation_seconds,
                "strategies": [
                    self._signal_payload(Signal("IV_SKEW", None, None, None, "WAITING", reason)),
                    self._signal_payload(Signal("IV_FLIP", None, None, None, "WAITING", reason)),
                    self._signal_payload(Signal("GAMMA_BLAST", None, None, None, "WAITING", reason)),
                ],
                "confluence": self._signal_payload(confluence_waiting),
                "data_ready": False,
                "signals_allowed": False,
            }

    def _evaluate_symbol(self, stream_id: str, config: ScannerConfig, now: datetime, now_ns: int) -> bool:
        symbol = stream_id
        feed = self._option_feeds.get(symbol)
        sampler = self._samplers.get(symbol)
        spot = self.spot_feed.spot_snapshot(config.symbol)
        if feed is None or sampler is None or spot is None or not hasattr(feed, "latest_quotes"):
            self._mark_waiting(symbol, now_ns, "fresh spot, option feed and sampler are all required")
            return True
        quotes = tuple(feed.latest_quotes())
        if not quotes:
            self._mark_waiting(symbol, now_ns, "no fresh option quotes are available")
            return True

        skew = evaluate_skew(quotes, spot.price, now, now_ns, config)
        # Match universe selection: derive ATM from the current live spot and
        # break exact midpoint ties upward, never from a cached prior ATM.
        atm_strike = min(
            {quote.strike for quote in quotes},
            key=lambda strike: (abs(strike - spot.price), -strike),
        )
        previous_diff = self._previous_atm_iv_diff.get(symbol)
        if self._previous_atm_strike.get(symbol) != atm_strike:
            previous_diff = None
        flip = evaluate_flip(
            quotes, spot.price, now, now_ns, config, previous_diff,
        )
        if flip.value is not None and flip.status != "WAITING":
            self._previous_atm_iv_diff[symbol] = flip.value
            self._previous_atm_strike[symbol] = atm_strike
        elif previous_diff is None:
            self._previous_atm_iv_diff.pop(symbol, None)
            self._previous_atm_strike[symbol] = atm_strike

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
                "data_ready": all(item.status != "WAITING" for item in (skew, flip)),
                # A directional output is allowed only after two independent
                # signal families agree. IV_SKEW and IV_FLIP alone are one
                # correlated IV-surface family and cannot authorize a signal.
                "signals_allowed": combined.status == "SIGNAL",
            }
        return True

    def _maybe_evaluate(self, now: datetime, now_ns: int) -> None:
        with self._lock:
            previous = self._last_evaluation_ns
        if previous is not None and now_ns - previous < int(self.evaluation_seconds * 1_000_000_000):
            return
        evaluated = False
        for stream_id, config in ACTIVE_CHAIN_CONFIGS:
            evaluated = self._evaluate_symbol(stream_id, config, now, now_ns) or evaluated
        if evaluated:
            with self._lock:
                self._last_evaluation_ns = now_ns

    @staticmethod
    def _persist_early_move_event(stream_id: str, config: ScannerConfig, spot_price: float, result: dict[str, Any]) -> None:
        """Persist only detector state transitions; repeated refreshes are idempotent."""
        state = str(result.get("state") or "").upper()
        direction = str(result.get("direction") or "").upper()
        if state not in {"PRE_MOVE", "CONFIRMED", "INVALIDATED"} or direction not in {"BULLISH", "BEARISH"}:
            return
        event_timestamp_ns = result.get("updated_at_ns") if state == "INVALIDATED" else result.get("evidence_at_ns")
        if isinstance(event_timestamp_ns, bool) or not isinstance(event_timestamp_ns, int) or event_timestamp_ns <= 0:
            return
        session = SessionLocal()
        try:
            exists = session.query(IVSignalEvent.id).filter_by(
                stream_id=stream_id, state=state, direction=direction,
                event_timestamp_ns=event_timestamp_ns,
            ).first()
            if exists:
                return
            tokens = result.get("qualifying_tokens") or []
            session.add(IVSignalEvent(
                stream_id=stream_id,
                underlying=config.symbol,
                expiry_type=config.expiry_type,
                state=state,
                direction=direction,
                event_timestamp_ns=event_timestamp_ns,
                source_timestamp_ns=result.get("source_timestamp_ns"),
                spot_timestamp_ns=result.get("spot_timestamp_ns"),
                spot_price=float(spot_price),
                volume_ratio=result.get("volume_ratio"),
                premium_change_pct=result.get("premium_change_pct"),
                iv_change_points=result.get("iv_change_points"),
                spot_change_pct=result.get("spot_change_pct"),
                contracts_confirming=int(result.get("contracts_confirming") or 0),
                qualifying_tokens=json.dumps(sorted({str(token) for token in tokens})),
                reason=str(result.get("reason") or ""),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _apply_equity_pressure_confirmation(self, underlying: str, result: dict[str, Any]) -> dict[str, Any]:
        """Require same-direction, fresh constituent pressure when dual-index mode is enabled."""
        if not self.equity_pressure_enabled or not self.equity_baskets:
            return result
        pressure = self._equity_pressure_by_underlying.get(str(underlying).upper())
        if not isinstance(pressure, dict):
            pressure = {}
        output = dict(result)
        output["constituent_pressure_status"] = pressure.get("status", "WAITING_FOR_FEED")
        output["constituent_pressure_direction"] = pressure.get("direction")
        output["constituent_pressure_signals_allowed"] = pressure.get("signals_allowed") is True
        state = str(output.get("state") or "").upper()
        direction = str(output.get("direction") or "").upper()
        if state not in {"PRE_MOVE", "CONFIRMED"}:
            return output
        if pressure.get("signals_allowed") is not True:
            output["option_signal_state"] = state
            output["state"] = "WATCH"
            output["signals_allowed"] = False
            output["reason"] = (
                "option early-move evidence is present; waiting for complete, fresh "
                f"{underlying} constituent pressure confirmation"
            )
            return output
        pressure_direction = str(pressure.get("direction") or "").upper()
        if pressure_direction != direction:
            output["option_signal_state"] = state
            output["state"] = "INVALIDATED"
            output["signals_allowed"] = False
            output["reason"] = (
                f"option direction {direction} conflicts with {underlying} "
                f"constituent pressure {pressure_direction or 'WAITING'}"
            )
            return output
        output["constituent_pressure_confirmed"] = True
        output["reason"] = f"{output.get('reason', '')}; confirmed by {underlying} constituent pressure"
        return output

    def _evaluate_early_move(self, now_ns: int) -> None:
        """Run the short-window alert path every refresh, independently of 60s signals."""
        for stream_id, config in ACTIVE_CHAIN_CONFIGS:
            feed = self._option_feeds.get(stream_id)
            sampler = self._samplers.get(stream_id)
            spot = self.spot_feed.spot_snapshot(config.symbol)
            if feed is None or sampler is None or spot is None:
                self._early_move[stream_id] = {
                    "state": "WATCH", "direction": None, "signals_allowed": False,
                    "reason": "waiting for fresh spot, option feed and sampler",
                    "baseline_status": "WARMING",
                }
                continue
            try:
                sampler_snapshot = sampler.snapshot()
                if stream_id == "NIFTY_WEEKLY":
                    expiry = sampler.configured_expiry
                    self._early_entry_alerts.evaluate(
                        sampler=sampler, spot=spot, expiry=expiry,
                        now=self.clock_datetime(), now_ns=now_ns,
                    )
                result = self._early_move_detectors[stream_id].update(
                    quotes=feed.latest_quotes(),
                    metrics=sampler_snapshot.get("raw_live_metrics", ()),
                    spot_price=spot.price,
                    now_ns=now_ns,
                    spot_timestamp_ns=getattr(spot, "timestamp_ns", None),
                )
                result = self._apply_equity_pressure_confirmation(config.symbol, result)
                previous = self._early_move.get(stream_id, {})
                is_transition = (
                    result.get("state") != previous.get("state")
                    or result.get("direction") != previous.get("direction")
                    or result.get("evidence_at_ns") != previous.get("evidence_at_ns")
                )
                self._early_move[stream_id] = result
                if is_transition and result.get("state") in {"PRE_MOVE", "CONFIRMED", "INVALIDATED"}:
                    try:
                        self._persist_early_move_event(stream_id, config, spot.price, result)
                        result["history_persistence"] = "SAVED"
                    except Exception as exc:
                        # Persistence health must not suppress a valid live detector alert.
                        result["history_persistence"] = "ERROR"
                        result["history_persistence_error"] = f"{type(exc).__name__}: {exc}"
                    self._early_move[stream_id] = result
            except Exception as exc:
                self._early_move[stream_id] = {
                    "state": "WATCH", "direction": None, "signals_allowed": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "baseline_status": "WARMING",
                }

    def _refresh_basis_analysis(self, now_ns: int) -> None:
        """Publish paired live spot/future basis for current and next expiries."""
        future_snapshot = self.future_feed.snapshot(now_ns=now_ns)
        by_underlying: dict[str, list[dict[str, Any]]] = {"NIFTY": [], "BANKNIFTY": []}
        quotes = future_snapshot.get("quotes", {})
        for contract in self._future_contracts:
            quote = quotes.get(contract.token, {})
            spot = self.spot_feed.spot_snapshot(contract.underlying, now_ns=now_ns)
            if spot is None or not quote.get("fresh"):
                by_underlying[contract.underlying].append({
                    "expiry": contract.expiry.isoformat(), "symbol": contract.symbol,
                    "status": "WAITING_FOR_FRESH_SPOT_OR_FUTURE", "signals_allowed": False,
                })
                continue
            try:
                result = calculate_basis(
                    spot=spot.price, future=quote.get("price"),
                    spot_timestamp_ns=spot.timestamp_ns,
                    future_timestamp_ns=quote.get("timestamp_ns"), now_ns=now_ns,
                )
            except (TypeError, ValueError) as exc:
                by_underlying[contract.underlying].append({
                    "expiry": contract.expiry.isoformat(), "symbol": contract.symbol,
                    "status": "INVALID_QUOTE", "signals_allowed": False, "error": str(exc),
                })
                continue
            result.update({"expiry": contract.expiry.isoformat(), "symbol": contract.symbol})
            history = self._basis_histories.get(f"{contract.underlying}:{contract.expiry.isoformat()}")
            if history is not None and result.get("fresh"):
                history.add({**result, "timestamp_ns": max(spot.timestamp_ns, quote["timestamp_ns"])})
                result["history"] = history.snapshot(now_ns)
            by_underlying[contract.underlying].append(result)
        self._basis_analysis = {
            symbol: {
                "status": "LIVE" if any(item.get("status") == "LIVE" for item in items) else
                          "STALE" if items and any(item.get("status") == "STALE" for item in items) else
                          "WAITING_FOR_TICKS",
                "expiry_contracts": items,
                "signals_allowed": False,
                "note": "Context only; never independently authorizes an IV signal or order.",
            }
            for symbol, items in by_underlying.items()
        }

    def _refresh_equity_pressure(self, now_ns: int) -> None:
        if self.equity_pressure_enabled and self.equity_baskets:
            # The shared feed has one quote per unique symbol. Fan those quotes
            # into both independent weighted pressure calculators without
            # fetching/subscribing a common constituent twice.
            shared_feed = self._equity_feed
            shared_error: Exception | None = None
            try:
                shared_snapshot = (
                    shared_feed.snapshot(now_ns=now_ns) if shared_feed is not None
                    else {"status": "STOPPED", "quotes": {}, "signals_allowed": False}
                )
            except Exception as exc:
                shared_error = exc
                shared_snapshot = {"status": "ERROR", "quotes": {}, "signals_allowed": False}
            shared_quotes = shared_snapshot.get("quotes", {})
            snapshots: dict[str, dict[str, Any]] = {}
            for underlying, basket in self.equity_baskets.items():
                pressure = self._equity_pressures[underlying]
                expected = sorted(item.symbol for item in basket)
                quotes = {symbol: shared_quotes[symbol] for symbol in expected if symbol in shared_quotes}
                fresh_symbols = sorted(quotes)
                missing_symbols = sorted(set(expected) - set(fresh_symbols))
                shared_status = shared_snapshot.get("status", "WAITING_FOR_TICKS")
                feed_status = (
                    "LIVE" if not missing_symbols else
                    "PARTIAL" if fresh_symbols else
                    "WAITING_FOR_TICKS" if shared_status == "PARTIAL" else
                    shared_status
                )
                feed_allowed = bool(expected) and not missing_symbols
                try:
                    if shared_error is not None:
                        raise shared_error
                    for symbol, quote in quotes.items():
                        pressure.update(ComponentSample(
                            symbol=symbol, timestamp_ns=quote["timestamp_ns"], ltp=quote["ltp"]
                        ))
                    pressure_snapshot = pressure.snapshot(now_ns=now_ns)
                    allowed = bool(
                        feed_allowed
                        and pressure_snapshot.get("signals_allowed")
                        and pressure_snapshot.get("direction") in {"BULLISH", "BEARISH"}
                        and pressure_snapshot.get("timeframe_agreement")
                    )
                    snapshots[underlying] = {
                        **pressure_snapshot,
                        "enabled": True,
                        "feed": {
                            "status": feed_status,
                            "consumer": shared_snapshot.get("consumer"),
                            "expected_symbols": expected,
                            "fresh_symbols": fresh_symbols,
                            "missing_symbols": missing_symbols,
                            "signals_allowed": feed_allowed,
                            "shared_unique_symbols": shared_snapshot.get("expected_symbols", []),
                        },
                        "signals_allowed": allowed,
                    }
                except Exception as exc:
                    snapshots[underlying] = {
                        "status": "ERROR",
                        "enabled": True,
                        "direction": "WAITING",
                        "timeframe_agreement": False,
                        "windows": {},
                        "signals_allowed": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "feed": {
                            "status": "ERROR",
                            "consumer": getattr(shared_feed, "consumer", None),
                            "signals_allowed": False,
                            "expected_symbols": expected,
                            "fresh_symbols": fresh_symbols,
                            "missing_symbols": missing_symbols,
                        },
                    }
            with self._lock:
                self._equity_pressure_by_underlying = snapshots
                self._equity_pressure_last_snapshot = snapshots.get("NIFTY", {
                    "status": "WAITING_FOR_FEED", "enabled": True, "signals_allowed": False,
                    "direction": "WAITING", "timeframe_agreement": False, "windows": {},
                })
            return
        if not self.equity_pressure_enabled or self._equity_feed is None or self._equity_pressure is None:
            with self._lock:
                self._equity_pressure_last_snapshot = {
                    "status": "UNAVAILABLE" if self.equity_pressure_unavailable_reason else "DISABLED",
                    "reason": self.equity_pressure_unavailable_reason,
                    "signals_allowed": False, "direction": "WAITING",
                    "timeframe_agreement": False, "windows": {},
                }
            return
        feed_snapshot = self._equity_feed.snapshot(now_ns=now_ns)
        for symbol, quote in feed_snapshot.get("quotes", {}).items():
            self._equity_pressure.update(ComponentSample(
                symbol=symbol,
                timestamp_ns=quote["timestamp_ns"],
                ltp=quote["ltp"],
            ))
        pressure_snapshot = self._equity_pressure.snapshot(now_ns=now_ns)
        signals_allowed = bool(
            feed_snapshot.get("signals_allowed")
            and pressure_snapshot.get("signals_allowed")
            and pressure_snapshot.get("direction") in {"BULLISH", "BEARISH"}
            and pressure_snapshot.get("timeframe_agreement")
        )
        with self._lock:
            self._equity_pressure_last_snapshot = {
                **pressure_snapshot,
                "enabled": True,
                "feed": {
                    "status": feed_snapshot.get("status"),
                    "consumer": feed_snapshot.get("consumer"),
                    "expected_symbols": feed_snapshot.get("expected_symbols", []),
                    "fresh_symbols": feed_snapshot.get("fresh_symbols", []),
                    "missing_symbols": feed_snapshot.get("missing_symbols", []),
                    "signals_allowed": bool(feed_snapshot.get("signals_allowed")),
                },
                "signals_allowed": signals_allowed,
            }

    def refresh_once(self) -> dict[str, Any]:
        now = self.clock_datetime()
        if now.tzinfo is None:
            raise ValueError("runtime datetime must be timezone-aware")
        for stream_id, config in ACTIVE_CHAIN_CONFIGS:
            spot = self.spot_feed.spot_snapshot(config.symbol)
            using_last_spot = False
            if spot is None:
                # A stale last-known spot may anchor the next expiry's ATM
                # strike ladder and subscriptions while the exchange is closed.
                # The sampler and signal engine still call spot_snapshot(), so
                # stale data can never qualify as a live signal.
                last_spot = getattr(self.spot_feed, "last_spot_snapshot", None)
                spot = last_spot(config.symbol) if callable(last_spot) else None
                if spot is None:
                    with self._lock:
                        self._last_error[stream_id] = (
                            f"WAITING_FOR_SPOT: {config.symbol} has no last-known "
                            "spot snapshot; option universe refresh is paused"
                        )
                    continue
                using_last_spot = True
            self._refresh_stream(stream_id, config, spot, now)
            # _refresh_stream clears transient errors after a successful
            # subscription. Re-apply this explicit health state afterwards so
            # a stale quote cannot make the chain appear fully live.
            if using_last_spot:
                with self._lock:
                    self._last_error[stream_id] = (
                        f"USING_LAST_SPOT_FOR_UNIVERSE_ONLY: {config.symbol} "
                        "market is closed or spot is stale; live sampling/signals remain blocked"
                    )
        now_ns = self.clock_ns()
        self._refresh_basis_analysis(now_ns)
        self._refresh_equity_pressure(now_ns)
        self._evaluate_early_move(now_ns)
        self._maybe_evaluate(now, now_ns)
        with self._lock:
            self._last_refresh_ns = now_ns
        return self.snapshot()

    @staticmethod
    def _telegram_sender(payload: dict[str, Any]) -> None:
        """Send one persisted IV alert through Telegram Bot API; no secrets enter logs."""
        token = str(settings.TELEGRAM_BOT_TOKEN or "").strip()
        chat_id = str(settings.TELEGRAM_CHAT_ID or "").strip()
        if not settings.TELEGRAM_ENABLED or not token or not chat_id:
            raise RuntimeError("TELEGRAM_CONFIGURATION_MISSING")
        observed_ns = int(payload.get("observed_at_ns") or 0)
        observed = datetime.fromtimestamp(observed_ns / 1_000_000_000, tz=IST).strftime("%d-%b-%Y %H:%M:%S IST") if observed_ns else "—"
        direction = str(payload.get("direction") or "—").upper()
        emoji = "🟢" if direction == "BULLISH" else "🔴" if direction == "BEARISH" else "⚠️"
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        checks = metadata.get("checks") if isinstance(metadata.get("checks"), dict) else {}
        change_times = checks.get("change_timestamps_ns") if isinstance(checks.get("change_timestamps_ns"), dict) else {}
        history_times = checks.get("history_timestamps_ns") if isinstance(checks.get("history_timestamps_ns"), dict) else {}
        wall_history_times = checks.get("wall_history_timestamps_ns")
        def format_change_time(value: Any) -> str:
            try:
                stamp = int(value)
                return datetime.fromtimestamp(stamp / 1_000_000_000, tz=IST).strftime("%H:%M:%S")
            except (TypeError, ValueError, OverflowError, OSError):
                return "—"
        time_labels = {
            "ce_quote": "CE", "pe_quote": "PE", "target_quote": "Target",
            "target_oi": "Target OI", "wall_source": "Wall", "wall_oi": "Wall OI",
            "entry_quote": "Entry", "entry_oi": "Entry OI",
            "rejection_candle": "Rejection",
        }
        change_time_text = " • ".join(
            f"{label} {format_change_time(change_times[key])}"
            for key, label in time_labels.items() if key in change_times
        )
        if isinstance(history_times, dict):
            for key in ("ce", "pe", "target"):
                values = history_times.get(key)
                if isinstance(values, list) and values:
                    stamps = [format_change_time(item.get("quote")) for item in values if isinstance(item, dict)]
                    change_time_text += f" • {key.upper()} history " + ",".join(stamps)
        if isinstance(wall_history_times, list) and wall_history_times:
            stamps = [format_change_time(item.get("quote")) for item in wall_history_times if isinstance(item, dict)]
            change_time_text += " • Wall history " + ",".join(stamps)
        lines = [
            f"{emoji} <b>NIFTY WEEKLY IV EARLY ENTRY</b>",
            f"<b>Strategy:</b> {escape(str(payload.get('strategy') or '—'))}",
            f"<b>Signal:</b> {escape(direction)} • {escape(str(payload.get('option_type') or '—'))}",
            f"<b>Contract:</b> {escape(str(payload.get('strike') or '—'))} • Expiry {escape(str(payload.get('expiry') or '—'))}",
            f"<b>Spot:</b> {escape(str(payload.get('spot') or '—'))} • Premium: {escape(str(payload.get('premium') or '—'))}",
            f"<b>IV Δ:</b> {escape(str(payload.get('iv_change_points') if payload.get('iv_change_points') is not None else '—'))} pt • Volume: {escape(str(payload.get('volume_ratio') if payload.get('volume_ratio') is not None else '—'))}x",
            f"<b>Alert time:</b> {escape(observed)}",
            f"<b>Change times (IST):</b> {escape(change_time_text or 'Source timestamps saved in alert history')}",
            f"<b>Reason:</b> {escape(str(payload.get('reason') or 'Conditions satisfied'))}",
            f"<b>Alert ID:</b> {int(payload.get('event_id') or 0)} • Alert only; no order placed",
        ]
        body = json.dumps({"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML", "disable_web_page_preview": True}).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Telegram rate limits are retryable. Preserve its Retry-After
            # hint without exposing response URLs, headers, or bot credentials.
            if exc.code == 429:
                retry_after = None
                # Telegram commonly places retry_after in responseParameters
                # JSON; proxies may additionally expose a Retry-After header.
                try:
                    raw_retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    if raw_retry_after is not None:
                        parsed = float(raw_retry_after)
                        if math.isfinite(parsed) and parsed >= 0:
                            retry_after = parsed
                except (TypeError, ValueError):
                    retry_after = None
                if retry_after is None:
                    try:
                        rejection = json.loads(exc.read().decode("utf-8"))
                        raw_retry_after = rejection.get("parameters", {}).get("retry_after")
                        parsed = float(raw_retry_after)
                        if math.isfinite(parsed) and parsed >= 0:
                            retry_after = parsed
                    except (AttributeError, UnicodeDecodeError, ValueError, TypeError):
                        retry_after = None
                raise TelegramRateLimitError(retry_after) from None
            # A 4xx (except 429 above) is a definitive configuration/request
            # rejection. A 5xx may be transient, so let the outbox retry it.
            if 400 <= exc.code < 500:
                raise TelegramRejectedError("TELEGRAM_API_REJECTED") from None
            raise RuntimeError("TELEGRAM_PROVIDER_TEMPORARY_FAILURE") from None
        except Exception:
            # Timeout, connection reset, or unreadable response may occur after
            # Telegram accepted the POST. Retrying could duplicate the alert.
            raise AmbiguousDeliveryError("TELEGRAM_DELIVERY_OUTCOME_UNKNOWN") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise TelegramRejectedError("TELEGRAM_API_REJECTED")

    def _process_telegram_outbox(self, now_ns: int) -> None:
        configured = bool(
            settings.TELEGRAM_ENABLED
            and str(settings.TELEGRAM_BOT_TOKEN or "").strip()
            and str(settings.TELEGRAM_CHAT_ID or "").strip()
        )
        try:
            result = self._telegram_outbox.process_one(
                now_ns=now_ns,
                sender=self._telegram_sender if configured else None,
                configured=configured,
            )
            if result.get("processed"):
                self._telegram_delivery = {
                    "status": str(result.get("status") or "UNKNOWN"),
                    "last_attempt_ns": now_ns,
                    "last_error": None,
                }
            elif not configured:
                self._telegram_delivery = {
                    "status": "WAITING_CONFIG",
                    "last_attempt_ns": self._telegram_delivery.get("last_attempt_ns"),
                    "last_error": "Set TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
                }
        except Exception as exc:
            # Delivery failures are isolated from the market-data and signal loop.
            self._telegram_delivery = {
                "status": "ERROR",
                "last_attempt_ns": now_ns,
                "last_error": f"{type(exc).__name__}: {exc}"[:500],
            }

    def _run_telegram_notifications(self) -> None:
        """Deliver outbox messages independently so Telegram latency never blocks market data."""
        while not self._stop.is_set():
            self._process_telegram_outbox(self.clock_ns())
            self._stop.wait(1.0)

    def _run(self) -> None:
        next_run = monotonic()
        while not self._stop.is_set():
            try:
                self.refresh_once()
                with self._lock:
                    self._last_error.pop("RUNTIME", None)
            except Exception as exc:
                # A transient master/feed/API error must not kill the refresh
                # worker permanently. Keep the runtime observable and retry
                # on the next bounded cadence.
                with self._lock:
                    self._last_error["RUNTIME"] = f"{type(exc).__name__}: {exc}"
            next_run += self.refresh_seconds
            delay = max(0.0, next_run - monotonic())
            if self._stop.wait(delay):
                break
            if delay == 0.0 and monotonic() - next_run > self.refresh_seconds:
                next_run = monotonic()

    def broker_snapshot_context(self, stream_id: str = "NIFTY_WEEKLY") -> tuple[Any, Any]:
        """Return the active IV universe and last spot observation for broker snapshots."""
        with self._lock:
            feed = self._option_feeds.get(str(stream_id).strip().upper())
        if feed is None:
            raise RuntimeError(f"IV option feed {stream_id} is not initialized")
        universe = feed.resolved_universe()
        if universe is None:
            raise RuntimeError(f"IV option universe {stream_id} is not resolved")
        spot = self.spot_feed.last_spot_snapshot(universe.underlying)
        return universe, spot

    def snapshot(self) -> dict[str, Any]:
        # Capture one manager view and reuse it in the spot diagnostics. Calling
        # snapshot twice could otherwise report a transient socket retirement
        # between the top-level connection block and the nested spot block.
        manager_snapshot = (
            self.feed_manager.snapshot()
            if self.feed_manager is not None
            else None
        )
        with self._lock:
            started = self._started
            feeds = dict(self._option_feeds)
            samplers = dict(self._samplers)
            errors = dict(self._last_error)
            signals = dict(self._signals)
            early_move = dict(self._early_move)
            last_refresh = self._last_refresh_ns
            last_evaluation = self._last_evaluation_ns
            equity_pressure = dict(self._equity_pressure_last_snapshot)
            equity_pressure_by_underlying = {
                symbol: dict(value) for symbol, value in self._equity_pressure_by_underlying.items()
            }
            basis_analysis = {symbol: dict(value) for symbol, value in self._basis_analysis.items()}
        return {
            "status": "STOPPING" if self._stopping else ("RUNNING" if started else "STOPPED"),
            "consumer": "iv-skew-flip",
            "orders_enabled": False,
            "market_data_connection": (
                {"isolation": "DEDICATED", "manager": manager_snapshot,
                 "socket_mode": 3, "socket_limit": 1}
                if self.feed_manager is not None
                else {"isolation": "SHARED_COMPATIBILITY_MODE"}
            ),
            "refresh_seconds": self.refresh_seconds,
            "signal_evaluation_seconds": self.evaluation_seconds,
            "last_evaluation_ns": last_evaluation,
            "signals": signals,
            "early_move": early_move,
            "early_entry_alerts": self._early_entry_alerts.snapshot(),
            "telegram_delivery": dict(self._telegram_delivery),
            "telegram_configured": bool(settings.TELEGRAM_ENABLED and str(settings.TELEGRAM_BOT_TOKEN or "").strip() and str(settings.TELEGRAM_CHAT_ID or "").strip()),
            "equity_pressure": {
                **equity_pressure,
                "enabled": self.equity_pressure_enabled,
            },
            "equity_pressure_by_underlying": equity_pressure_by_underlying,
            "spot_feed": (
                self.spot_feed.snapshot(transport_snapshot=manager_snapshot)
                if isinstance(self.spot_feed, IVSpotFeedAdapter)
                else self.spot_feed.snapshot()
            ),
            "index_future_feed": self.future_feed.snapshot(),
            "spot_future_basis": basis_analysis,
            "enabled_streams": [stream_id for stream_id, _ in ACTIVE_CHAIN_CONFIGS],
            "option_chains": {
                stream_id: {
                    "symbol": config.symbol,
                    "expiry_type": config.expiry_type,
                    "enabled": stream_id in {key for key, _ in ACTIVE_CHAIN_CONFIGS},
                    "status": (
                        "DISABLED_BY_SCOPE" if stream_id not in {key for key, _ in ACTIVE_CHAIN_CONFIGS}
                        else "WAITING_FOR_SPOT"
                    ) if stream_id not in feeds else feeds[stream_id].snapshot().get("status", "WAITING"),
                    "option_feed": feeds[stream_id].snapshot() if stream_id in feeds else {"status": "DISABLED_BY_SCOPE" if stream_id not in {key for key, _ in ACTIVE_CHAIN_CONFIGS} else "WAITING_FOR_SPOT"},
                    "sampler": samplers[stream_id].snapshot() if stream_id in samplers else {"status": "DISABLED_BY_SCOPE" if stream_id not in {key for key, _ in ACTIVE_CHAIN_CONFIGS} else "WAITING"},
                    "universe_tokens": len(self._universe_tokens.get(stream_id, ())),
                    "expiry_selection": dict(self._expiry_diagnostics.get(stream_id, {})),
                    "error": errors.get(stream_id),
                    "signals": signals.get(stream_id),
                    "early_move": early_move.get(stream_id),
                }
                for stream_id, config in ALL_CHAIN_CONFIGS
            },
            "indices": {
                "NIFTY": {
                    "option_feed": feeds["NIFTY_WEEKLY"].snapshot() if "NIFTY_WEEKLY" in feeds else {"status": "WAITING_FOR_SPOT"},
                    "sampler": samplers["NIFTY_WEEKLY"].snapshot() if "NIFTY_WEEKLY" in samplers else {"status": "WAITING"},
                    "universe_tokens": len(self._universe_tokens.get("NIFTY_WEEKLY", ())),
                    "error": errors.get("NIFTY_WEEKLY"),
                    "signals": signals.get("NIFTY_WEEKLY"),
                },
                "BANKNIFTY": {"status": "DISABLED_BY_SCOPE", "universe_tokens": 0, "signals_allowed": False},
            },
            "last_refresh_ns": last_refresh,
            "last_error": errors.get("RUNTIME"),
            "cleanup_errors": errors.get("CLEANUP"),
        }

    def stop(self) -> dict[str, Any]:
        with self._lifecycle_lock:
            return self._stop_locked()

    def _stop_locked(self) -> dict[str, Any]:
        self._stop.set()
        thread = self._thread
        telegram_thread = self._telegram_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        if telegram_thread is not None and telegram_thread.is_alive():
            telegram_thread.join(timeout=6.0)
        if thread is not None and thread.is_alive():
            # Do not tear down feeds while refresh_once() may still be using
            # them. Keep the runtime registered and allow a later stop retry.
            with self._lock:
                self._stopping = True
            return self.snapshot()
        if telegram_thread is not None and telegram_thread.is_alive():
            # A sender may still be completing an external Telegram request.
            # Do not clear its thread reference or permit restart to clear the
            # stop event while this worker is alive; that could create two
            # concurrent outbox consumers after a slow shutdown.
            with self._lock:
                self._stopping = True
            return self.snapshot()

        with self._lock:
            samplers = tuple(self._samplers.items())
            feeds = tuple(self._option_feeds.items())
            equity_feed = self._equity_feed

        # Try every independently-owned consumer even when another stop fails.
        # Retain all references until every cleanup call succeeds so OFF can retry.
        cleanup_errors: dict[str, str] = {}

        def stop_resource(name: str, resource: Any) -> None:
            try:
                resource.stop()
            except Exception as exc:
                cleanup_errors[name] = f"{type(exc).__name__}: {exc}"

        for name, sampler in samplers:
            stop_resource(f"sampler:{name}", sampler)
        for name, feed in feeds:
            stop_resource(f"option_feed:{name}", feed)
        stop_resource("spot_feed", self.spot_feed)
        stop_resource("future_feed", self.future_feed)
        if equity_feed is not None:
            stop_resource("equity_feed", equity_feed)

        if not cleanup_errors and self.feed_manager is not None:
            try:
                self.feed_manager.close()
            except Exception as exc:
                cleanup_errors["dedicated_feed_manager"] = f"{type(exc).__name__}: {exc}"

        if cleanup_errors:
            with self._lock:
                self._stopping = True
                self._last_error["CLEANUP"] = json.dumps(cleanup_errors, sort_keys=True)
            return self.snapshot()

        with self._lock:
            self._started = False
            self._stopping = False
            self._thread = None
            self._telegram_thread = None
            self._last_error.pop("CLEANUP", None)
            self._samplers.clear()
            self._option_feeds.clear()
            self._equity_feed = None
            self._universe_tokens.clear()
            self._rows_by_stream.clear()
            self._future_contracts = ()
            self._basis_histories.clear()
            self._basis_analysis.clear()
            self._signals.clear()
            self._early_move.clear()
            if self.equity_pressure_enabled and self.equity_basket:
                self._equity_pressure = EquityComponentPressure(
                    tuple(ComponentWeight(item.symbol, item.weight) for item in self.equity_basket)
                )
            self._equity_pressures = {
                symbol: EquityComponentPressure(
                    tuple(ComponentWeight(item.symbol, item.weight) for item in items)
                )
                for symbol, items in self.equity_baskets.items()
            }
            self._equity_pressure_by_underlying = {
                symbol: {
                    "status": "STOPPED", "enabled": True, "signals_allowed": False,
                    "direction": "WAITING", "timeframe_agreement": False, "windows": {},
                }
                for symbol in self.equity_baskets
            }
            self._equity_pressure_last_snapshot = {
                "status": "DISABLED" if not self.equity_pressure_enabled else "STOPPED",
                "signals_allowed": False, "direction": "WAITING",
                "timeframe_agreement": False, "windows": {},
            }
            self._early_move_detectors = {stream_id: IVEarlyMoveDetector() for stream_id, _ in ACTIVE_CHAIN_CONFIGS}
            self._previous_atm_iv_diff.clear()
            self._previous_atm_strike.clear()
            self._last_evaluation_ns = None
        return self.snapshot()



__all__ = ["IVSkewFlipRuntime"]
