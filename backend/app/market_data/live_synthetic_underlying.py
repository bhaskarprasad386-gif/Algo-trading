"""Live underlying-price feed backed by the common market-data manager."""
from __future__ import annotations

from threading import Event, Lock
from time import time_ns
from typing import Callable

from app.algo.auth import AngelOneAuth
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker
from app.core.logger import app_logger

BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})
TOKEN_REFRESH_SECONDS = 30.0
UNDERLYING_STALE_AFTER_SECONDS = 10.0


def filter_resolvable_index_symbols(instrument_master: InstrumentMaster, symbols) -> tuple[str, ...]:
    """Keep only index underlyings with a concrete Angel One index token."""
    result = []
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            continue
        exchange = "BSE" if symbol in BSE_INDEX_SYMBOLS else "NSE"
        try:
            token = instrument_master.resolve_index_token(symbol, exchange)
        except (LookupError, ValueError):
            continue
        if token:
            result.append(symbol)
    return tuple(dict.fromkeys(result))


class LiveSyntheticUnderlyingFeed:
    """Subscribe to real underlying tokens through the shared feed."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        tracker: LiveSyntheticAtmTracker,
        instrument_master: InstrumentMaster | None = None,
        auth: AngelOneAuth | None = None,
        on_price: Callable[[str, float, int | None], None] | None = None,
        concrete_tokens: dict[str, str] | None = None,
        index_symbols: frozenset[str] = frozenset(),
        commodity_symbols: frozenset[str] = frozenset(),
        consumer: str = "synthetic-underlyings",
    ) -> None:
        if not symbols:
            raise ValueError("at least one underlying symbol is required")
        self.symbols = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not self.symbols:
            raise ValueError("underlying symbols must be non-empty")
        self.tracker = tracker
        self.instrument_master = instrument_master or InstrumentMaster()
        self.auth = auth or AngelOneAuth()
        self.on_price = on_price
        self.concrete_tokens = {
            str(k).strip().upper(): str(v).strip()
            for k, v in (concrete_tokens or {}).items()
            if str(k).strip() and str(v).strip()
        }
        self.consumer = str(consumer).strip()
        if not self.consumer:
            raise ValueError("consumer is required")
        self.commodity_symbols = frozenset(str(symbol).strip().upper() for symbol in commodity_symbols if str(symbol).strip())
        self.index_symbols = frozenset(
            str(symbol).strip().upper() for symbol in index_symbols if str(symbol).strip()
        )
        self.stop_event = Event()
        self._feed: CommonStrategyMarketFeed | None = None
        # The shared registry may keep another consumer's canonical display
        # symbol (for example RELIANCE-EQ) for the same broker token. Track the
        # configured underlying by instrument identity so ATM updates do not
        # depend on whichever consumer registered the descriptor first.
        self._symbol_by_instrument: dict[tuple[str, str, str], str] = {}
        self._diagnostics_lock = Lock()
        self._last_resolved_tokens: dict[str, str] = {}
        self._last_token_resolution_at_ns: int | None = None
        self._last_tick_by_symbol: dict[str, dict[str, int | float]] = {}

    def _needs_instrument_master(self) -> bool:
        """Return whether any configured symbol still needs lookup-based resolution."""
        return any(symbol not in self.concrete_tokens for symbol in self.symbols)

    @staticmethod
    def _price(message: dict) -> float | None:
        try:
            value = float(message.get("last_traded_price"))
        except (TypeError, ValueError):
            return None
        return value / 100.0 if value > 0 else None

    @staticmethod
    def _timestamp_ns(message: dict) -> int | None:
        try:
            value = int(float(message.get("exchange_timestamp")))
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        if value < 10_000_000_000:
            return value * 1_000_000_000
        if value < 10_000_000_000_000:
            return value * 1_000_000
        return value * 1_000

    def _tokens(self) -> dict[str, str]:
        """Resolve every configured symbol without dropping unresolved targets.

        Empty token values are intentionally retained so the Synthetic runner
        keeps the target in its logical universe and can recover it after the
        instrument master becomes resolvable again.
        """
        # An authoritative token supplied by the caller is already resolved;
        # downloading the entire instrument master adds no value and can turn
        # an offline token-resolution path into a network timeout.
        if self._needs_instrument_master():
            self.instrument_master.download()
        result = {}
        for symbol in self.symbols:
            token = ""
            try:
                token = self.concrete_tokens.get(symbol, "")
                if not token and symbol in self.index_symbols:
                    exchange = "BSE" if symbol in BSE_INDEX_SYMBOLS else "NSE"
                    token = self.instrument_master.resolve_index_token(symbol, exchange) or ""
                elif not token and symbol in self.commodity_symbols:
                    token = self.instrument_master.get_token(symbol, "MCX") or ""
                elif not token:
                    token = self.instrument_master.resolve_cash_token(symbol, "NSE") or ""
            except (LookupError, ValueError) as exc:
                app_logger.warning(
                    "Synthetic underlying unresolved symbol {}: {}",
                    symbol,
                    exc,
                )
            result[symbol] = str(token).strip()
        with self._diagnostics_lock:
            self._last_resolved_tokens = dict(result)
            self._last_token_resolution_at_ns = time_ns()
        return result

    def _exchange_type(self, symbol: str) -> int:
        if symbol in self.commodity_symbols: return 5
        return 4 if symbol in BSE_INDEX_SYMBOLS and symbol in self.index_symbols else 1

    def _subscription_groups(self, tokens: dict[str, str]) -> dict[int, list[str]]:
        groups = {}
        for symbol, token in tokens.items():
            if not token:
                continue
            groups.setdefault(self._exchange_type(symbol), []).append(token)
        return groups

    def _descriptors(self):
        # _tokens() owns the single instrument-master refresh for this cycle.
        # Downloading here as well doubled the network work before every subscribe.
        tokens = self._tokens()
        descriptors = []
        symbol_by_instrument: dict[tuple[str, str, str], str] = {}
        for symbol, token in tokens.items():
            if not token:
                continue
            if symbol in self.commodity_symbols:
                exchange = "MCX"
            elif symbol in BSE_INDEX_SYMBOLS and symbol in self.index_symbols:
                exchange = "BSE"
            else:
                exchange = "NSE"
            descriptor = CommonStrategyMarketFeed.descriptor(
                exchange=exchange,
                token=token,
                symbol=symbol,
                instrument_type="index" if symbol in self.index_symbols else "equity",
                segment=exchange,
            )
            descriptors.append(descriptor)
            key = descriptor.key
            symbol_by_instrument[
                (key.exchange.strip().upper(), key.segment.strip().upper(), key.token.strip())
            ] = symbol
        self._symbol_by_instrument = symbol_by_instrument
        return descriptors

    def _on_record(self, record) -> None:
        instrument = getattr(record, "instrument", None)
        if instrument is not None:
            identity = (
                str(getattr(instrument, "exchange", "")).strip().upper(),
                str(getattr(instrument, "segment", "")).strip().upper(),
                str(getattr(instrument, "token", "")).strip(),
            )
            symbol = self._symbol_by_instrument.get(identity, "")
        else:
            symbol = ""
        # A canonical record may carry the symbol registered by another shared
        # consumer, not this strategy's underlying name. Fall back to that
        # display symbol only when it is one of our configured underlyings.
        if not symbol:
            candidate = str(getattr(record, "symbol", "")).strip().upper()
            if candidate in self.symbols:
                symbol = candidate
        if not symbol:
            return
        price = record.ltp
        if price is None or price <= 0:
            return
        timestamp_ns = record.timestamp_ns
        received_at_ns = time_ns()
        self.tracker.update(symbol, price, timestamp_ns)
        with self._diagnostics_lock:
            self._last_tick_by_symbol[symbol] = {
                "price": float(price),
                "source_timestamp_ns": int(timestamp_ns) if timestamp_ns else 0,
                "received_at_ns": received_at_ns,
            }
        if self.on_price is not None:
            self.on_price(symbol, price, timestamp_ns)

    def run_forever(self) -> None:
        """Keep retrying the instrument master so temporarily missing tokens recover."""
        self._feed = CommonStrategyMarketFeed(
            self.consumer,
            auth=self.auth,
        )
        active_tokens: dict[str, str] = {}
        try:
            while not self.stop_event.is_set():
                try:
                    descriptors = self._descriptors()
                    # _descriptors() already resolved every configured symbol and
                    # recorded the result for diagnostics. Re-resolving here used to
                    # download the same instrument master a second time per cycle.
                    with self._diagnostics_lock:
                        current_token_set = {
                            symbol: token
                            for symbol, token in self._last_resolved_tokens.items()
                            if token
                        }
                    if not descriptors:
                        # If resolution regresses after a previously successful
                        # cycle, actively release the old subscription set. Keeping
                        # it registered would make the feed look subscribed while
                        # all current targets are unresolved.
                        if active_tokens:
                            self._feed.stop()
                            active_tokens = {}
                        app_logger.warning("Synthetic underlying feed found no resolvable live symbols; retrying")
                        self.stop_event.wait(TOKEN_REFRESH_SECONDS)
                        continue
                    if current_token_set != active_tokens:
                        if active_tokens:
                            self._feed.stop()
                        self._feed.start(descriptors, self._on_record)
                        active_tokens = current_token_set
                    if self.stop_event.wait(TOKEN_REFRESH_SECONDS):
                        break
                except Exception:
                    # Instrument-master/network and broker-subscription failures are
                    # transient in production. Do not let one exception silently kill
                    # the only worker that can deliver ATM prices to the scanner.
                    app_logger.exception(
                        "Synthetic underlying feed cycle failed; retrying in %.1fs",
                        TOKEN_REFRESH_SECONDS,
                    )
                    if self.stop_event.wait(TOKEN_REFRESH_SECONDS):
                        break
        finally:
            if self._feed:
                self._feed.stop()
                self._feed = None

    def snapshot(self) -> dict:
        """Return non-persistent runtime telemetry for this feed."""
        now_ns = time_ns()
        with self._diagnostics_lock:
            tokens = dict(self._last_resolved_tokens)
            resolved_at_ns = self._last_token_resolution_at_ns
            last_ticks = {symbol: dict(value) for symbol, value in self._last_tick_by_symbol.items()}
        symbol_health = {}
        for symbol in self.symbols:
            token = tokens.get(symbol, "")
            tick = last_ticks.get(symbol)
            received_at_ns = int(tick["received_at_ns"]) if tick else None
            source_timestamp_ns = int(tick["source_timestamp_ns"]) if tick else 0
            receive_age_seconds = (
                max(0, now_ns - received_at_ns) / 1_000_000_000
                if received_at_ns is not None else None
            )
            source_age_seconds = (
                (now_ns - source_timestamp_ns) / 1_000_000_000
                if source_timestamp_ns > 0 else None
            )
            # A frame arriving now is not fresh if its exchange timestamp is
            # old. ATM tracking rejects source timestamps older than its configured freshness window.
            # future-skewed timestamps; diagnostics must agree instead of
            # reporting LIVE solely from a recent receive time.
            if receive_age_seconds is None:
                effective_age_seconds = None
            elif source_age_seconds is None:
                effective_age_seconds = receive_age_seconds
            elif source_age_seconds < -1.0:
                effective_age_seconds = max(
                    receive_age_seconds, UNDERLYING_STALE_AFTER_SECONDS + 1.0
                )
            else:
                effective_age_seconds = max(
                    receive_age_seconds, max(0.0, source_age_seconds)
                )
            # Report health per symbol, not only at feed level. A single
            # healthy index must not make every configured stock appear live.
            if not token:
                symbol_state = "TOKEN_MISSING"
                symbol_reason = "underlying_token_unresolved"
            elif effective_age_seconds is None:
                symbol_state = "WAITING_FOR_TICK"
                symbol_reason = "no_underlying_tick_received"
            elif effective_age_seconds <= UNDERLYING_STALE_AFTER_SECONDS:
                symbol_state = "LIVE"
                symbol_reason = "fresh_underlying_tick"
            else:
                symbol_state = "STALE"
                symbol_reason = "underlying_tick_exceeded_freshness_threshold"
            symbol_health[symbol] = {
                "state": symbol_state,
                "state_reason": symbol_reason,
                "token_resolved": bool(token),
                "token": token or None,
                "last_price": tick["price"] if tick else None,
                "last_tick_age_seconds": (
                    round(effective_age_seconds, 3)
                    if effective_age_seconds is not None else None
                ),
                "receive_age_seconds": (
                    round(receive_age_seconds, 3)
                    if receive_age_seconds is not None else None
                ),
                "source_timestamp_age_seconds": (
                    round(max(0.0, source_age_seconds), 3)
                    if source_age_seconds is not None else None
                ),
                "last_tick_received_at_ns": received_at_ns,
                "source_timestamp_ns": source_timestamp_ns or None,
            }
        feed_snapshot = self._feed.snapshot() if self._feed is not None else {}
        resolved_count = sum(1 for item in symbol_health.values() if item["token_resolved"])
        tick_ages = [
            item["last_tick_age_seconds"]
            for item in symbol_health.values()
            if item["last_tick_age_seconds"] is not None
        ]
        fresh_tick_count = sum(
            1 for age in tick_ages if age <= UNDERLYING_STALE_AFTER_SECONDS
        )
        close_timed_out_groups = feed_snapshot.get("transport_close_timed_out_groups") or []
        disconnected_groups = feed_snapshot.get("disconnected_groups") or []
        if resolved_at_ns is None:
            state = "STARTING"
            state_reason = "token_resolution_not_attempted"
        elif not resolved_count:
            state = "TOKEN_RESOLUTION_FAILED"
            state_reason = "no_configured_symbol_has_a_resolved_token"
        elif close_timed_out_groups:
            state = "FAILED"
            state_reason = "transport_close_timed_out"
        elif fresh_tick_count:
            state = "LIVE"
            state_reason = "at_least_one_underlying_has_a_fresh_tick"
        elif tick_ages:
            state = "STALE"
            state_reason = "all_observed_underlying_ticks_exceeded_freshness_threshold"
        elif bool(feed_snapshot.get("connected")):
            state = "CONNECTED_NO_TICKS"
            state_reason = "transport_connected_without_an_accepted_underlying_tick"
        elif self._feed is not None:
            state = "RECONNECTING"
            state_reason = (
                "required_transport_disconnected"
                if disconnected_groups
                else "transport_not_yet_connected"
            )
        else:
            state = "STARTING"
            state_reason = "feed_worker_not_started"
        return {
            "started": self._feed is not None,
            "consumer": self.consumer,
            "state": state,
            "state_reason": state_reason,
            "stale_after_seconds": UNDERLYING_STALE_AFTER_SECONDS,
            "resolved_symbol_count": resolved_count,
            "configured_symbol_count": len(self.symbols),
            "fresh_symbol_count": fresh_tick_count,
            "token_resolution_at_ns": resolved_at_ns,
            "symbols": symbol_health,
            "feed": feed_snapshot if self._feed is not None else None,
        }

    def stop(self) -> None:
        self.stop_event.set()
        if self._feed:
            self._feed.stop()


__all__ = ["LiveSyntheticUnderlyingFeed"]