"""Live underlying-price feed backed by the common market-data manager."""
from __future__ import annotations

from threading import Event
from typing import Callable

from app.algo.auth import AngelOneAuth
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker

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
                result[symbol] = self.instrument_master.resolve_index_token(symbol, exchange)
            elif symbol in self.commodity_symbols:
                result[symbol] = self.instrument_master.get_token(symbol, "MCX") or ""
                if not result[symbol]:
                    raise LookupError(f"no MCX underlying token for {symbol}")
                else:
                    token = self.instrument_master.resolve_cash_token(symbol, "NSE")
                    if token:
                        result[symbol] = token
            except (LookupError, ValueError) as exc:
                app_logger.warning("Synthetic underlying skipped unresolved symbol %s: %s", symbol, exc)
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
                exchange = "BSE" if symbol in BSE_INDEX_SYMBOLS else "NSE"
            else:
                exchange = "MCX"
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

    def _on_record(self, record) -> None:
        symbol = record.symbol.strip().upper()
        price = record.ltp
        if price is None or price <= 0:
            return
        timestamp_ns = record.timestamp_ns
        self.tracker.update(symbol, price)
        if self.on_price is not None:
            self.on_price(symbol, price, timestamp_ns)

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
