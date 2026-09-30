"""Live underlying-price feed backed by the common market-data manager."""
from __future__ import annotations

from threading import Event
from typing import Callable

from app.algo.auth import AngelOneAuth
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker

BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})


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
        self.index_symbols = frozenset(
            str(symbol).strip().upper() for symbol in index_symbols if str(symbol).strip()
        )
        self.stop_event = Event()
        self._feed: CommonStrategyMarketFeed | None = None

    def _descriptors(self):
        self.instrument_master.download()
        descriptors = []
        for symbol in self.symbols:
            token = self.concrete_tokens.get(symbol)
            if token:
                exchange = "BFO" if symbol in BSE_INDEX_SYMBOLS and symbol in self.index_symbols else (
                    "NFO" if symbol in self.index_symbols else "NSE"
                )
            elif symbol in self.index_symbols:
                exchange = "BSE" if symbol in BSE_INDEX_SYMBOLS else "NSE"
                token = self.instrument_master.resolve_index_token(symbol, exchange)
            else:
                exchange = "NSE"
                token = self.instrument_master.resolve_cash_token(symbol, exchange)
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
            "synthetic-underlyings",
            auth=self.auth,
        )
        self._feed.start(self._descriptors(), self._on_record)
        try:
            while not self.stop_event.wait(1.0):
                pass
        finally:
            if self._feed:
                self._feed.stop()
                self._feed = None

    def stop(self) -> None:
        self.stop_event.set()
        if self._feed:
            self._feed.stop()


__all__ = ["LiveSyntheticUnderlyingFeed"]
