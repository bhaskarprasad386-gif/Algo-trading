"""Fresh four-leg depth snapshots for Box Spread paper execution.

This consumer attaches to the existing process-wide Angel One manager. It does
not open a socket, alter scanner subscriptions, or touch the synthetic runner.
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import Condition, RLock
from time import monotonic
from typing import Any, Iterator

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor
from app.scanner.box_spread_contract_resolver import ResolvedBoxSpreadContracts


_CONSUMER = "box-paper-execution-depth"
_ROLES = ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")


class BoxPaperDepthFeedError(RuntimeError):
    """The shared feed could not provide a synchronized, fresh four-leg quote."""


class BoxPaperDepthFeed:
    """Serialize short-lived exact-token subscriptions on the common feed."""

    def __init__(self, *, mode: int = 3, max_age_seconds: float = 5.0,
                 max_skew_seconds: float = 2.0) -> None:
        self._feed = CommonStrategyMarketFeed(_CONSUMER, mode=mode)
        self._max_age = float(max_age_seconds)
        self._max_skew = float(max_skew_seconds)
        self._condition = Condition(RLock())
        self._records: dict[str, tuple[Any, float]] = {}
        self._keys: dict[InstrumentKey, str] = {}

    @staticmethod
    def _descriptors(contracts: ResolvedBoxSpreadContracts) -> tuple[InstrumentDescriptor, ...]:
        return tuple(
            CommonStrategyMarketFeed.descriptor(
                exchange=contracts.exchange_segment,
                token=leg.token,
                symbol=leg.symbol,
                instrument_type="option",
                segment=contracts.exchange_segment,
                expiry=leg.expiry,
                strike=leg.strike,
                option_type=leg.option_type,
                lot_size=leg.lot_size,
                underlying=leg.underlying,
            )
            for leg in contracts.legs
        )

    def _on_record(self, record: Any) -> None:
        instrument = getattr(record, "instrument", None)
        if instrument is None:
            return
        with self._condition:
            role = self._keys.get(instrument)
            if role is not None:
                # Arrival time is tracked independently of provider timestamps:
                # the latter may be absent or malformed in broker frames.
                self._records[role] = (record, monotonic())
                self._condition.notify_all()

    @contextmanager
    def snapshot(self, contracts: ResolvedBoxSpreadContracts,
                 *, timeout_seconds: float = 3.0) -> Iterator[dict[str, Any]]:
        """Yield one synchronized fresh record per exact resolved contract."""
        descriptors = self._descriptors(contracts)
        keys = tuple(item.key for item in descriptors)
        if len(keys) != 4 or len(set(keys)) != 4:
            raise BoxPaperDepthFeedError("four unique exact option contracts are required")

        with self._condition:
            self._records.clear()
            self._keys = {descriptor.key: role for descriptor, role in zip(descriptors, _ROLES)}
        try:
            self._feed.start(descriptors, self._on_record)
            deadline = monotonic() + max(0.1, float(timeout_seconds))
            snapshot_records = None
            with self._condition:
                while snapshot_records is None:
                    now = monotonic()
                    fresh = {
                        role: pair for role, pair in self._records.items()
                        if now - pair[1] <= self._max_age
                    }
                    if len(fresh) == 4:
                        arrivals = [pair[1] for pair in fresh.values()]
                        if max(arrivals) - min(arrivals) <= self._max_skew:
                            snapshot_records = {role: pair[0] for role, pair in fresh.items()}
                            break
                    remaining = deadline - now
                    if remaining <= 0:
                        missing = sorted(set(_ROLES) - set(fresh))
                        if not missing and len(fresh) == 4:
                            raise BoxPaperDepthFeedError(
                                "four-leg depth ticks are not synchronized"
                            )
                        raise BoxPaperDepthFeedError(
                            "fresh depth unavailable for: " + ", ".join(missing or _ROLES)
                        )
                    self._condition.wait(min(remaining, 0.1))
            yield snapshot_records
        finally:
            self._feed.stop()
            with self._condition:
                self._keys.clear()
                self._records.clear()


_shared_feed = BoxPaperDepthFeed()
_snapshot_lock = RLock()


@contextmanager
def fresh_box_paper_depth(contracts: ResolvedBoxSpreadContracts,
                          *, timeout_seconds: float = 3.0) -> Iterator[dict[str, Any]]:
    """Get a serialized fresh snapshot without creating another broker socket."""
    with _snapshot_lock:
        with _shared_feed.snapshot(contracts, timeout_seconds=timeout_seconds) as records:
            yield records


__all__ = ["BoxPaperDepthFeed", "BoxPaperDepthFeedError", "fresh_box_paper_depth"]
