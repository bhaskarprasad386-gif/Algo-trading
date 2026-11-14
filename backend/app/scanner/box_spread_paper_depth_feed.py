"""Fresh four-leg depth snapshots for Box Spread paper execution.

This consumer attaches to the existing process-wide Angel One manager. It does
not open a socket, alter scanner subscriptions, or touch the synthetic runner.
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import Condition, RLock
from time import monotonic, time_ns
from typing import Any, Iterator

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor
from app.scanner.box_spread_contract_resolver import ResolvedBoxSpreadContracts


_CONSUMER = "box-paper-execution-depth"
_ROLES = ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")
_PROVIDER_TIMESTAMP_FIELDS = (
    "exchange_timestamp_ns", "timestamp_ns", "exchange_timestamp",
    "exchange_timestamp_ms", "timestamp", "feed_time",
)
_PROVIDER_MAX_FUTURE_SKEW_SECONDS = 1.0


class BoxPaperDepthFeedError(RuntimeError):
    """The shared feed could not provide a synchronized, fresh four-leg quote."""


class BoxPaperDepthFeed:
    """Serialize short-lived exact-token subscriptions on the common feed."""

    def __init__(self, *, mode: int = 3, max_age_seconds: float = 5.0,
                 max_skew_seconds: float = 5.0) -> None:
        self._feed = CommonStrategyMarketFeed(_CONSUMER, mode=mode, warm_start=True)
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

    @staticmethod
    def _provider_timestamp_ns(record: Any) -> int | None:
        """Return a provider timestamp only when the raw broker frame supplied one.

        MarketDataRecord.timestamp_ns may be a local receipt-time fallback. Do
        not mistake that fallback for exchange time. Missing provider time is
        treated as unknown (not an automatic rejection); arrival freshness
        remains available as a compatibility fallback.
        """
        payload = getattr(record, "payload", None)
        if not isinstance(payload, dict) or not any(
            payload.get(name) is not None for name in _PROVIDER_TIMESTAMP_FIELDS
        ):
            return None
        # Parse the raw provider field itself. The normalizer deliberately
        # falls back to local receipt time for malformed timestamps, so reading
        # record.timestamp_ns here would incorrectly bless malformed provider
        # data as an exchange timestamp.
        for name in ("exchange_timestamp_ns", "timestamp_ns"):
            raw = payload.get(name)
            if raw is None:
                continue
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError, OverflowError):
                continue
            return value if value > 0 else None

        raw = next(
            (payload.get(name) for name in (
                "exchange_timestamp", "exchange_timestamp_ms", "timestamp", "feed_time"
            ) if payload.get(name) is not None),
            None,
        )
        if raw is None:
            return None
        try:
            text = str(raw).strip()
            integer = int(text)
        except (TypeError, ValueError, OverflowError):
            try:
                number = float(raw)
            except (TypeError, ValueError, OverflowError):
                return None
            if not (number == number and abs(number) != float("inf")):
                return None
            integer = int(number)

        magnitude = abs(integer)
        if magnitude >= 10**16:
            value = integer
        elif magnitude >= 10**14:
            value = integer * 1_000
        elif magnitude >= 10**11:
            value = integer * 1_000_000
        else:
            value = integer * 1_000_000_000
        return value if value > 0 else None

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
                    wall_now_ns = time_ns()
                    fresh = {}
                    provider_times = []
                    for role, pair in self._records.items():
                        record, arrived = pair
                        if now - arrived > self._max_age:
                            continue
                        provider_ns = self._provider_timestamp_ns(record)
                        if provider_ns is not None:
                            provider_age = (wall_now_ns - provider_ns) / 1e9
                            # Reject clearly stale/replayed provider frames, but
                            # allow small exchange/server clock lead.
                            if not (-_PROVIDER_MAX_FUTURE_SKEW_SECONDS <= provider_age <= self._max_age):
                                continue
                            provider_times.append(provider_ns)
                        fresh[role] = pair
                    if len(fresh) == 4:
                        arrivals = [pair[1] for pair in fresh.values()]
                        arrivals_ok = max(arrivals) - min(arrivals) <= self._max_skew
                        # Compare provider clocks only when all four frames
                        # actually carry provider timestamps. If one broker
                        # frame omits it, do not lose an otherwise usable
                        # opportunity solely because of that omission.
                        provider_times_complete = len(provider_times) == 4
                        provider_skew_ok = (
                            not provider_times_complete
                            or max(provider_times) - min(provider_times) <= self._max_skew * 1e9
                        )
                        if arrivals_ok and provider_skew_ok:
                            snapshot_records = {role: pair[0] for role, pair in fresh.items()}
                            break
                    remaining = deadline - now
                    if remaining <= 0:
                        missing = sorted(set(_ROLES) - set(fresh))
                        if not missing and len(fresh) == 4:
                            raise BoxPaperDepthFeedError(
                                "four-leg depth ticks did not synchronize within the retry window"
                            )
                        raise BoxPaperDepthFeedError(
                            "fresh depth unavailable for: " + ", ".join(missing or _ROLES)
                        )
                    self._condition.wait(min(remaining, 0.1))
            yield snapshot_records
        finally:
            # Also clear if start() failed after callback registration but
            # before CommonStrategyMarketFeed marked itself started.
            self._feed.stop()
            self._feed.manager.clear_consumer(_CONSUMER)
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
