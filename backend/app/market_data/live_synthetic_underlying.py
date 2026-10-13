"""Live underlying-price feed backed by the common market-data manager."""
from __future__ import annotations

from math import isfinite
from threading import Event, Lock
from typing import Callable

from app.algo.auth import AngelOneAuth
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker
from app.core.logger import app_logger

BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})


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
        self._price_lock = Lock()
        self._latest_timestamp_by_symbol: dict[str, int] = {}

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
        self.instrument_master.download()
        result = {}
        for symbol in self.symbols:
            try:
                token = self.concrete_tokens.get(symbol)
                if token:
                    result[symbol] = token
                elif symbol in self.index_symbols:
                    exchange = "BSE" if symbol in BSE_INDEX_SYMBOLS else "NSE"
                    token = self.instrument_master.resolve_index_token(symbol, exchange)
                    if token:
                        result[symbol] = token
                elif symbol in self.commodity_symbols:
                    token = self.instrument_master.get_token(symbol, "MCX") or ""
                    if token:
                        result[symbol] = token
                else:
                    token = self.instrument_master.resolve_cash_token(symbol, "NSE")
                    if token:
                        result[symbol] = token
            except (LookupError, ValueError) as exc:
                app_logger.warning(
                    "Synthetic underlying skipped unresolved symbol {}: {}",
                    symbol,
                    exc,
                )
        return result

    def _exchange_type(self, symbol: str) -> int:
        if symbol in self.commodity_symbols: return 5
        return 4 if symbol in BSE_INDEX_SYMBOLS and symbol in self.index_symbols else 1

    def _subscription_groups(self, tokens: dict[str, str]) -> dict[int, list[str]]:
        groups = {}
        for symbol, token in tokens.items():
            groups.setdefault(self._exchange_type(symbol), []).append(token)
        return groups

    def _descriptors(self):
        self.instrument_master.download()
        tokens = self._tokens()
        descriptors = []
        for symbol, token in tokens.items():
            if symbol in self.commodity_symbols:
                exchange = "MCX"
            elif symbol in BSE_INDEX_SYMBOLS and symbol in self.index_symbols:
                exchange = "BSE"
            else:
                exchange = "NSE"
            descriptors.append(
                CommonStrategyMarketFeed.descriptor(
                    exchange=exchange,
                    token=token,
                    symbol=symbol,
                    instrument_type="index" if symbol in self.index_symbols else "equity",
                    segment=exchange,
                )
            )
        return descriptors

    def _apply_price(self, symbol: str, price: object, timestamp_ns: object) -> bool:
        symbol = str(symbol).strip().upper()
        try:
            normalized_price = float(price)
            normalized_timestamp = int(timestamp_ns)
        except (TypeError, ValueError, OverflowError):
            return False
        if (
            not symbol
            or not isfinite(normalized_price)
            or normalized_price <= 0
            or normalized_timestamp <= 0
        ):
            return False
        with self._price_lock:
            previous_timestamp = self._latest_timestamp_by_symbol.get(symbol)
            if previous_timestamp is not None and normalized_timestamp <= previous_timestamp:
                return False
            self._latest_timestamp_by_symbol[symbol] = normalized_timestamp
            self.tracker.update(symbol, normalized_price)
        if self.on_price is not None:
            self.on_price(symbol, normalized_price, normalized_timestamp)
        return True

    def _on_record(self, record) -> None:
        self._apply_price(record.symbol, record.ltp, record.timestamp_ns)

    def _seed_from_cache(self, descriptors) -> int:
        """Seed the underlying tracker from fresh canonical snapshots on late start."""
        feed = self._feed
        if feed is None:
            return 0
        seeded = 0
        for descriptor in descriptors:
            snapshot = feed.latest_snapshot(descriptor.key)
            if not isinstance(snapshot, dict):
                continue
            # Guard against malformed or mismatched cache entries. Cache TTL is
            # enforced by latest_snapshot(); timestamp ordering prevents a cache
            # read racing a newer live callback from rolling the tracker back.
            if (
                str(snapshot.get("exchange") or "").strip().upper() != descriptor.key.exchange.strip().upper()
                or str(snapshot.get("segment") or "").strip().upper() != descriptor.key.segment.strip().upper()
                or str(snapshot.get("token") or "").strip() != descriptor.key.token.strip()
                or str(snapshot.get("symbol") or "").strip().upper() != descriptor.symbol.strip().upper()
            ):
                continue
            if self._apply_price(snapshot.get("symbol"), snapshot.get("ltp"), snapshot.get("timestamp_ns")):
                seeded += 1
        return seeded

    def run_forever(self) -> None:
        self._feed = CommonStrategyMarketFeed(
            self.consumer,
            auth=self.auth,
        )
        descriptors = self._descriptors()
        if not descriptors:
            app_logger.warning("Synthetic underlying feed found no resolvable live symbols")
            return
        self._feed.start(descriptors, self._on_record)
        self._seed_from_cache(descriptors)
        try:
            while not self.stop_event.wait(1.0):
                pass
        finally:
            if self._feed:
                self._feed.stop()
                self._feed = None

    def snapshot(self) -> dict:
        """Return non-persistent runtime telemetry for this feed."""
        if self._feed is None:
            return {"started": False, "consumer": self.consumer}
        snapshot = self._feed.snapshot()
        return {"started": True, "consumer": self.consumer, "feed": snapshot}

    def stop(self) -> None:
        self.stop_event.set()
        if self._feed:
            self._feed.stop()


__all__ = ["LiveSyntheticUnderlyingFeed"]
