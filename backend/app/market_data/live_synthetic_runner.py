"""Production wiring for the live synthetic arbitrage stream.

This module composes the already-tested contract selector, bounded Angel One
WebSocket recorder, live scanner and durable alert pipeline. It does not place
broker orders. The caller must provide the authoritative stock universe and
current ATM values; this module never invents either.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from time import sleep, time_ns
from typing import Callable, Iterable

from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.core.database import SessionLocal
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_stream import LiveSyntheticOptionFutureRecorder
from app.market_data.synthetic_subscriptions import select_synthetic_contracts
from app.market_data.live_synthetic_underlying import LiveSyntheticUnderlyingFeed
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker, concrete_strikes_from_master
from app.scanner.live_synthetic_pipeline import LiveSyntheticScanPipeline
from app.scanner.live_synthetic_scanner import LiveSyntheticScanner
from app.scanner.synthetic_cash_carry import SyntheticScanConfig


@dataclass(frozen=True)
class SyntheticLiveTarget:
    """One underlying/class to stream from the real Angel One instrument master."""

    underlying: str
    instrument_class: str
    atm_strike: float | None = None
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
        self.session_factory = session_factory
        self.policy = policy or ScanPolicy()
        self.on_results = on_results
        self.underlying_feed = underlying_feed
        self._atm_tracker: LiveSyntheticAtmTracker | None = None
        self._atm_provider = atm_provider
        self._recorder: LiveSyntheticOptionFutureRecorder | None = None
        self._refresh_requested = Event()


    def concrete_atm_strikes(self) -> dict[str, tuple[float, ...]]:
        """Return concrete option strikes from the selected target expiries."""
        self.instrument_master.download()
        result: dict[str, tuple[float, ...]] = {}
        for target in self.targets:
            expiry = target.expiry
            if not expiry:
                continue
            result.update(
                concrete_strikes_from_master(
                    self.instrument_master.instruments,
                    symbols=(target.underlying,),
                    expiry=expiry,
                )
            )
        return result
    def _ensure_atm_provider(self) -> Callable[[str, int], float | None]:
        """Build a source-backed ATM provider when the caller did not supply one."""
        if self._atm_provider is not None:
            return self._atm_provider
        strikes = self.concrete_atm_strikes()
        missing = [
            target.underlying
            for target in self.targets
            if target.underlying.strip().upper() not in strikes
        ]
        if missing:
            raise LookupError(
                "no concrete option strikes found for live expiry: "
                + ", ".join(sorted(set(missing)))
            )
        self._atm_tracker = LiveSyntheticAtmTracker(strikes_by_symbol=strikes)
        return self._atm_tracker.atm

    def _ensure_underlying_feed(self) -> LiveSyntheticUnderlyingFeed | None:
        """Create the real underlying feed for automatic ATM tracking."""
        if self.underlying_feed is not None or self._atm_tracker is None:
            return self.underlying_feed
        symbols = tuple(dict.fromkeys(target.underlying.strip().upper() for target in self.targets))
        index_symbols = frozenset(
            target.underlying.strip().upper()
            for target in self.targets
            if target.instrument_class.strip().upper() == "INDEX"
        )
        return LiveSyntheticUnderlyingFeed(
            symbols,
            tracker=self._atm_tracker,
            instrument_master=self.instrument_master,
            auth=self.auth,
            index_symbols=index_symbols,
        )

    def build_subscriptions(self) -> tuple:
        """Resolve concrete current/near contracts from the Angel One master."""
        atm_provider = self._ensure_atm_provider()
        self.instrument_master.download()
        subscriptions = []
        seen: set[tuple[int, str]] = set()
        for target in self.targets:
            live_atm = atm_provider(target.underlying, time_ns())
            atm_strike = (
                live_atm
                if self._atm_tracker is not None
                else (live_atm or target.atm_strike)
            )
            if atm_strike is None:
                raise LookupError(
                    f"live ATM price is not available for {target.underlying}"
                )
            selection = select_synthetic_contracts(
                self.instrument_master,
                underlying=target.underlying,
                instrument_class=target.instrument_class,
                atm_strike=atm_strike,
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

    def _wait_for_live_atm(
        self,
        atm_provider: Callable[[str, int], float | None],
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        """Wait for real underlying ticks before selecting option strikes."""
        if self._atm_tracker is None:
            return
        deadline = time_ns() + int(timeout_seconds * 1_000_000_000)
        symbols = tuple(target.underlying.strip().upper() for target in self.targets)
        while time_ns() < deadline:
            if all(atm_provider(symbol, time_ns()) is not None for symbol in symbols):
                return
            sleep(0.25)
        missing = [
            symbol for symbol in symbols
            if atm_provider(symbol, time_ns()) is None
        ]
        raise TimeoutError(
            "timed out waiting for live underlying prices: "
            + ", ".join(sorted(set(missing)))
        )

    def run_forever(self) -> None:
        atm_provider = self._ensure_atm_provider()
        feed = self._ensure_underlying_feed()
        feed_thread = None
        if feed is not None:
            feed_thread = Thread(
                target=feed.run_forever,
                daemon=True,
                name="synthetic-underlying-feed",
            )
            feed_thread.start()
        try:
            self._wait_for_live_atm(atm_provider)
            while self._recorder is None or not self._recorder.stop_event.is_set():
                self._refresh_requested.clear()
                subscriptions = self.build_subscriptions()
                scanner = LiveSyntheticScanner(
                    atm_provider=atm_provider,
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
                    latest = atm_provider(symbol, time_ns())
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
                self._recorder = None
                break
        finally:
            if feed is not None:
                feed.stop()
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
