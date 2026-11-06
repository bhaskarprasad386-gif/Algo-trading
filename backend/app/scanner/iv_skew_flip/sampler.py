"""One-second IV/OI sampling over the IV scanner's shared-feed quotes.

The sampler never fabricates exchange ticks: every sample records the source
quote timestamp. Repeated UI samples may reuse a still-fresh quote, while the
five-minute OI history only advances when a newer broker tick arrives.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from threading import Event, RLock, Thread
from time import monotonic, time_ns
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .engine import DEFAULT_CONFIGS, OptionQuote, ScannerConfig, _expiry_years, implied_volatility


IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class SpotSnapshot:
    price: float
    timestamp_ns: int


@dataclass(frozen=True)
class OptionMetricSample:
    token: str
    underlying: str
    expiry: str
    strike: float
    option_type: str
    sampled_at_ns: int
    source_timestamp_ns: int
    oi: int | None
    mark: float | None
    iv: float | None

    @property
    def source_age_ms(self) -> float:
        return max(0.0, (self.sampled_at_ns - self.source_timestamp_ns) / 1_000_000.0)


class IVOneSecondSampler:
    """Calculate IV once per second and retain source-timestamped OI history."""

    def __init__(
        self,
        *,
        quote_source: Any,
        spot_provider: Callable[[], SpotSnapshot | tuple[float, int] | None],
        config: ScannerConfig | None = None,
        interval_seconds: float = 1.0,
        max_quote_age_seconds: float = 5.0,
        max_spot_age_seconds: float = 3.0,
        history_seconds: int = 600,
        clock_ns: Callable[[], int] = time_ns,
        clock_datetime: Callable[[], datetime] | None = None,
    ) -> None:
        if interval_seconds != 1.0:
            raise ValueError("IV/OI sampling cadence is fixed at one second")
        self.quote_source = quote_source
        self.spot_provider = spot_provider
        self.config = config or DEFAULT_CONFIGS[0]
        self.interval_seconds = interval_seconds
        self.max_quote_age_ns = int(max_quote_age_seconds * 1_000_000_000)
        self.max_spot_age_ns = int(max_spot_age_seconds * 1_000_000_000)
        self.history_limit = max(2, int(history_seconds))
        self.clock_ns = clock_ns
        self.clock_datetime = clock_datetime or (lambda: datetime.now(IST))
        self._lock = RLock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._latest: dict[str, OptionMetricSample] = {}
        self._history: dict[str, deque[OptionMetricSample]] = defaultdict(deque)
        self._last_source_timestamp: dict[str, int] = {}
        self._last_sampled_at_ns: int | None = None
        self._last_error: str | None = None

    @staticmethod
    def _spot(value: SpotSnapshot | tuple[float, int] | None) -> SpotSnapshot | None:
        if isinstance(value, SpotSnapshot):
            result = value
        elif isinstance(value, tuple) and len(value) == 2:
            if (
                isinstance(value[0], bool)
                or not isinstance(value[0], (int, float))
                or isinstance(value[1], bool)
                or not isinstance(value[1], int)
            ):
                return None
            try:
                result = SpotSnapshot(float(value[0]), value[1])
            except (TypeError, ValueError, OverflowError):
                return None
        else:
            return None
        if (
            isinstance(result.price, bool)
            or not isinstance(result.price, (int, float))
            or not isfinite(result.price)
            or result.price <= 0
            or isinstance(result.timestamp_ns, bool)
            or not isinstance(result.timestamp_ns, int)
            or result.timestamp_ns <= 0
        ):
            return None
        return result

    def sample_once(
        self,
        *,
        now_ns: int | None = None,
        now: datetime | None = None,
        spot: SpotSnapshot | tuple[float, int] | None = None,
    ) -> dict[str, Any]:
        sampled_at = self.clock_ns() if now_ns is None else now_ns
        sampled_datetime = self.clock_datetime() if now is None else now
        if isinstance(sampled_at, bool) or not isinstance(sampled_at, int) or sampled_at <= 0:
            raise ValueError("sample timestamp must be a positive integer nanosecond value")
        if sampled_datetime.tzinfo is None:
            raise ValueError("sampler datetime must be timezone-aware")
        spot_value = self._spot(self.spot_provider() if spot is None else spot)
        if spot_value is None or sampled_at - spot_value.timestamp_ns < 0 or sampled_at - spot_value.timestamp_ns > self.max_spot_age_ns:
            with self._lock:
                self._last_error = "fresh spot snapshot unavailable"
                self._last_sampled_at_ns = sampled_at
            return self.snapshot()

        years = _expiry_years(self.configured_expiry, sampled_datetime) if self.configured_expiry else None
        if years is None:
            with self._lock:
                self._last_error = "configured option expiry invalid or expired"
                self._last_sampled_at_ns = sampled_at
            return self.snapshot()

        quotes: tuple[OptionQuote, ...] = tuple(self.quote_source.latest_quotes())
        updates: dict[str, OptionMetricSample] = {}
        new_history: list[tuple[str, OptionMetricSample]] = []
        for quote in quotes:
            if quote.underlying.upper() != self.config.symbol or quote.expiry != self.configured_expiry:
                continue
            if (
                not quote.token
                or isinstance(quote.timestamp_ns, bool)
                or not isinstance(quote.timestamp_ns, int)
                or quote.timestamp_ns <= 0
                or sampled_at < quote.timestamp_ns
                or sampled_at - quote.timestamp_ns > self.max_quote_age_ns
            ):
                continue
            mark = quote.mark
            iv = None if mark is None else implied_volatility(
                quote.option_type, spot_value.price, quote.strike, years, mark,
                self.config.risk_free_rate, self.config.dividend_yield,
            )
            sample = OptionMetricSample(
                token=quote.token, underlying=quote.underlying, expiry=quote.expiry,
                strike=quote.strike, option_type=quote.option_type,
                sampled_at_ns=sampled_at, source_timestamp_ns=quote.timestamp_ns,
                oi=quote.oi if isinstance(quote.oi, int) and not isinstance(quote.oi, bool) and quote.oi >= 0 else None,
                mark=mark, iv=None if iv is None else round(iv * 100.0, 4),
            )
            updates[quote.token] = sample
            if self._last_source_timestamp.get(quote.token) != quote.timestamp_ns:
                new_history.append((quote.token, sample))

        with self._lock:
            self._latest = updates
            cutoff_ns = sampled_at - self.history_limit * 1_000_000_000
            for token, sample in new_history:
                history = self._history[token]
                history.append(sample)
                while history and history[0].source_timestamp_ns < cutoff_ns:
                    history.popleft()
                self._last_source_timestamp[token] = sample.source_timestamp_ns
            self._last_sampled_at_ns = sampled_at
            self._last_error = None if updates else "no fresh option quotes available"
        return self.snapshot()

    @property
    def configured_expiry(self) -> str | None:
        universe = getattr(self.quote_source, "_universe", None)
        return None if universe is None else universe.expiry

    def oi_reference(self, token: str, *, lookback_seconds: int = 300) -> OptionMetricSample | None:
        """Return the closest source-timestamped OI sample at/before 5m ago."""
        target = self.clock_ns() - lookback_seconds * 1_000_000_000
        with self._lock:
            history = tuple(self._history.get(token, ()))
        max_age_ns = (lookback_seconds + 60) * 1_000_000_000
        candidates = [
            item for item in history
            if item.source_timestamp_ns <= target
            and self.clock_ns() - item.source_timestamp_ns <= max_age_ns
            and item.oi is not None
        ]
        return max(candidates, key=lambda item: item.source_timestamp_ns) if candidates else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            samples = tuple(self._latest.values())
            history_sizes = {token: len(rows) for token, rows in self._history.items()}
            sampled_at = self._last_sampled_at_ns
            error = self._last_error
        return {
            "cadence_seconds": 1,
            "sampled_at_ns": sampled_at,
            "sample_count": len(samples),
            "oi_values": {item.token: item.oi for item in samples},
            "iv_values": {item.token: item.iv for item in samples},
            "samples": samples,
            "history_samples_by_token": history_sizes,
            "five_minute_oi_reference_available": sum(
                1 for item in samples if self.oi_reference(item.token) is not None
            ),
            "status": "LIVE" if samples and error is None else "WAITING",
            "reason": error,
        }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name="iv-skew-one-second-sampler", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        next_run = monotonic()
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception as exc:  # keep sampler alive; surface error in health
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
            next_run += self.interval_seconds
            delay = max(0.0, next_run - monotonic())
            if self._stop.wait(delay):
                break
            if delay == 0.0 and monotonic() - next_run > self.interval_seconds:
                next_run = monotonic()

    def stop(self, timeout_seconds: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout_seconds))
        with self._lock:
            if thread is None or not thread.is_alive():
                self._thread = None


__all__ = ["SpotSnapshot", "OptionMetricSample", "IVOneSecondSampler"]
