"""Production wiring for the live synthetic arbitrage stream.

This module composes the already-tested contract selector, bounded Angel One
WebSocket recorder, live scanner and durable alert pipeline. It does not place
broker orders. The caller must provide the authoritative stock universe and
current ATM values; this module never invents either.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from time import time_ns
from typing import Callable, Iterable

from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.core.database import SessionLocal
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_stream import LiveSyntheticOptionFutureRecorder
from app.market_data.synthetic_subscriptions import select_synthetic_contracts
from app.market_data.live_synthetic_underlying import LiveSyntheticUnderlyingFeed
from app.scanner.live_synthetic_pipeline import LiveSyntheticScanPipeline
from app.scanner.live_synthetic_scanner import LiveSyntheticScanner
from app.scanner.synthetic_cash_carry import SyntheticScanConfig


@dataclass(frozen=True)
class SyntheticLiveTarget:
    """One underlying/class to stream from the real Angel One instrument master."""

    underlying: str
    instrument_class: str
    atm_strike: float
    expiry: str | None = None


class LiveSyntheticRunner:
    """Run real option+future streaming and scanning for configured targets."""

    def __init__(
        self,
        data_db: str,
        targets: Iterable[SyntheticLiveTarget],
        *,
        allowed_stock_symbols: frozenset[str],
        instrument_master: InstrumentMaster | None = None,
        auth=None,
        scan_config_provider: Callable[[str], SyntheticScanConfig] | None = None,
        atm_provider: Callable[[str, int], float | None] | None = None,
        session_factory=SessionLocal,
        policy: ScanPolicy | None = None,
        on_results=None,
        underlying_feed: LiveSyntheticUnderlyingFeed | None = None,
    ) -> None:
        self.data_db = data_db
        self.targets = tuple(targets)
        if not self.targets:
            raise ValueError("at least one live synthetic target is required")
        self.allowed_stock_symbols = frozenset(
            str(symbol).strip().upper()
            for symbol in allowed_stock_symbols
            if str(symbol).strip()
        )
        self.instrument_master = instrument_master or InstrumentMaster()
        self.auth = auth
        self.scan_config_provider = scan_config_provider or (
            lambda _symbol: SyntheticScanConfig(
                allowed_stock_symbols=self.allowed_stock_symbols
            )
        )
        if atm_provider is None:
            raise ValueError("atm_provider is required for live synthetic scanning")
        self.atm_provider = atm_provider
        self.session_factory = session_factory
        self.policy = policy or ScanPolicy()
        self.on_results = on_results
        self.underlying_feed = underlying_feed
        self._recorder: LiveSyntheticOptionFutureRecorder | None = None
        self._refresh_requested = Event()

    def build_subscriptions(self) -> tuple:
        """Resolve concrete current/near contracts from the Angel One master."""
        self.instrument_master.download()
        subscriptions = []
        seen: set[tuple[int, str]] = set()
        for target in self.targets:
            selection = select_synthetic_contracts(
                self.instrument_master,
                underlying=target.underlying,
                instrument_class=target.instrument_class,
                atm_strike=(self.atm_provider(target.underlying, time_ns()) or target.atm_strike),
                expiry=target.expiry,
                allowed_stock_symbols=self.allowed_stock_symbols,
                policy=self.policy,
            )
            for item in selection.subscriptions:
                key = (item.exchange_type, item.token)
                if key not in seen:
                    seen.add(key)
                    subscriptions.append(item)
        return tuple(subscriptions)

    def run_forever(self) -> None:
        feed_thread = None
        if self.underlying_feed is not None:
            feed_thread = Thread(
                target=self.underlying_feed.run_forever,
                daemon=True,
                name="synthetic-underlying-feed",
            )
            feed_thread.start()
        try:
            while self._recorder is None or not self._recorder.stop_event.is_set():
                self._refresh_requested.clear()
                subscriptions = self.build_subscriptions()
                scanner = LiveSyntheticScanner(
                    atm_provider=self.atm_provider,
                    config_provider=self.scan_config_provider,
                )
                pipeline = LiveSyntheticScanPipeline(
                    scanner=scanner,
                    session_factory=self.session_factory,
                    on_results=self.on_results,
                )
                initial_atm = {
                    target.underlying: self.atm_provider(
                        target.underlying, time_ns()
                    )
                    for target in self.targets
                }

                def observe(payload):
                    pipeline.observe(payload)
                    symbol = str(payload.get("underlying") or "").upper()
                    current = initial_atm.get(symbol)
                    if current is None:
                        return
                    latest = self.atm_provider(symbol, time_ns())
                    if latest is not None and latest != current and self._recorder is not None:
                        self._refresh_requested.set()
                        self._recorder.stop()

                self._recorder = LiveSyntheticOptionFutureRecorder(
                    self.data_db,
                    list(subscriptions),
                    auth=self.auth,
                    on_observation=observe,
                )
                self._recorder.run_forever()
                refresh_requested = self._refresh_requested.is_set()
                if refresh_requested:
                    self._refresh_requested.clear()
                    self._recorder = None
                    continue
                if self._recorder.stop_event.is_set() and (
                    self.underlying_feed is None
                    or self.underlying_feed.stop_event.is_set()
                ):
                    break
                self._recorder = None
        finally:
            if self.underlying_feed is not None:
                self.underlying_feed.stop()
            if self._recorder is not None:
                self._recorder.stop()
                self._recorder = None

    def stop(self) -> None:
        if self.underlying_feed is not None:
            self.underlying_feed.stop()
        recorder = self._recorder
        if recorder is not None:
            recorder.stop()


__all__ = ["LiveSyntheticRunner", "SyntheticLiveTarget"]
