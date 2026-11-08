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

from app.models.iv_option_observation import IVOptionObservation

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
    spot_timestamp_ns: int
    spot_price: float | None
    oi: int | None
    mark: float | None
    ltp: float | None
    iv: float | None
    iv_input: str | None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None

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
        history_session_factory: Callable[[], Any] | None = None,
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
        self.history_session_factory = history_session_factory
        self._history_loaded = False
        self._history_persisted_count = 0
        self._history_persistence_error: str | None = None
        self._lock = RLock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._latest: dict[str, OptionMetricSample] = {}
        self._previous_cycle: dict[str, OptionMetricSample] = {}
        self._one_second_changes: dict[str, dict[str, float | int | None]] = {}
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

    def _restore_history(self, now_ns: int) -> int:
        if self.history_session_factory is None or self._history_loaded:
            return 0
        session = self.history_session_factory()
        try:
            cutoff = now_ns - self.history_limit * 1_000_000_000
            rows = (session.query(IVOptionObservation).filter(
                IVOptionObservation.underlying == self.config.symbol,
                IVOptionObservation.expiry == self.configured_expiry,
                IVOptionObservation.source_timestamp_ns >= cutoff,
                IVOptionObservation.source_timestamp_ns <= now_ns,
            ).order_by(IVOptionObservation.source_timestamp_ns.asc()).all())
            with self._lock:
                for row in rows:
                    sample = OptionMetricSample(
                        token=row.token, underlying=row.underlying, expiry=row.expiry,
                        strike=row.strike, option_type=row.option_type, sampled_at_ns=row.sampled_at_ns,
                        source_timestamp_ns=row.source_timestamp_ns, spot_timestamp_ns=row.spot_timestamp_ns,
                        spot_price=row.spot_price,
                        oi=row.oi, mark=row.mark, ltp=row.ltp, iv=row.iv,
                        iv_input='BID_ASK_MIDPOINT' if row.bid is not None and row.ask is not None else None,
                        bid=row.bid, ask=row.ask, volume=row.volume,
                    )
                    history = self._history[row.token]
                    if not history or history[-1].source_timestamp_ns < sample.source_timestamp_ns:
                        history.append(sample)
                    self._last_source_timestamp[row.token] = max(self._last_source_timestamp.get(row.token, 0), row.source_timestamp_ns)
                self._history_loaded = True
            return len(rows)
        finally:
            session.close()

    def _persist_history(self, samples: list[OptionMetricSample]) -> int:
        if self.history_session_factory is None or not samples:
            return 0
        session = self.history_session_factory()
        inserted = 0
        try:
            for item in samples:
                exists = session.query(IVOptionObservation.id).filter_by(token=item.token, source_timestamp_ns=item.source_timestamp_ns).first()
                if exists:
                    continue
                session.add(IVOptionObservation(
                    token=item.token, underlying=item.underlying, expiry=item.expiry, strike=item.strike,
                    option_type=item.option_type, source_timestamp_ns=item.source_timestamp_ns,
                    spot_timestamp_ns=item.spot_timestamp_ns, spot_price=getattr(item, "spot_price", None), sampled_at_ns=item.sampled_at_ns,
                    iv=item.iv, oi=item.oi, bid=item.bid, ask=item.ask, ltp=item.ltp, mark=item.mark, volume=item.volume,
                ))
                inserted += 1
            session.commit()
            self._history_persisted_count += inserted
            return inserted
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
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
        if self.history_session_factory is not None and not self._history_loaded and self.configured_expiry:
            self._restore_history(sampled_at)
        spot_value = self._spot(self.spot_provider() if spot is None else spot)
        if spot_value is None or sampled_at - spot_value.timestamp_ns < 0 or sampled_at - spot_value.timestamp_ns > self.max_spot_age_ns:
            with self._lock:
                # A failed spot gate invalidates every previously calculated
                # IV value: do not leave stale metrics exposed as LIVE.
                self._latest.clear()
                self._last_error = "fresh spot snapshot unavailable"
                self._last_sampled_at_ns = sampled_at
            return self.snapshot()

        years = _expiry_years(self.configured_expiry, sampled_datetime) if self.configured_expiry else None
        if years is None:
            with self._lock:
                self._latest.clear()
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
                spot_timestamp_ns=spot_value.timestamp_ns,
                spot_price=spot_value.price,
                oi=quote.oi if isinstance(quote.oi, int) and not isinstance(quote.oi, bool) and quote.oi >= 0 else None,
                mark=mark,
                ltp=quote.ltp if quote.ltp is not None and isfinite(quote.ltp) and quote.ltp > 0 else None,
                iv=None if iv is None else round(iv * 100.0, 4),
                iv_input=(
                    "BID_ASK_MIDPOINT"
                    if quote.bid is not None and quote.ask is not None
                    and isfinite(quote.bid) and isfinite(quote.ask)
                    and 0 < quote.bid <= quote.ask
                    and (quote.ask - quote.bid) * 100.0 / ((quote.ask + quote.bid) / 2.0) <= 8.0
                    else "LTP" if mark is not None and quote.ltp is not None
                    and isfinite(quote.ltp) and quote.ltp > 0
                    else None
                ),
                bid=quote.bid if quote.bid is not None and isfinite(quote.bid) and quote.bid > 0 else None,
                ask=quote.ask if quote.ask is not None and isfinite(quote.ask) and quote.ask > 0 else None,
                volume=quote.volume if isinstance(quote.volume, int) and not isinstance(quote.volume, bool) and quote.volume >= 0 else None,
            )
            updates[quote.token] = sample
            if self._last_source_timestamp.get(quote.token) != quote.timestamp_ns:
                new_history.append((quote.token, sample))

        # Serialize source-cursor checks, durable inserts and in-memory history
        # updates. Concurrent refreshes must not race past the same source tick.
        with self._lock:
            pending_history = [
                (token, sample) for token, sample in new_history
                if self._last_source_timestamp.get(token) != sample.source_timestamp_ns
            ]
            persistence_ok = True
            try:
                self._persist_history([sample for _, sample in pending_history])
                self._history_persistence_error = None
            except Exception as exc:
                # Persistence outages must be visible and retried, but must not
                # take down live IV sampling. Do not advance durable cursors.
                persistence_ok = False
                self._history_persistence_error = f"{type(exc).__name__}: {exc}"
            changes: dict[str, dict[str, float | int | None]] = {}
            for token, sample in updates.items():
                previous = self._previous_cycle.get(token)
                elapsed_ns = 0 if previous is None else sampled_at - previous.sampled_at_ns
                # Compare consecutive one-second sampler cycles only. A repeated
                # broker tick is not a new market observation and must not show
                # a misleading zero change.
                if previous is not None and 500_000_000 <= elapsed_ns <= 1_500_000_000 and (sample.source_timestamp_ns > previous.source_timestamp_ns or sample.spot_timestamp_ns > previous.spot_timestamp_ns):
                    new_option_tick = sample.source_timestamp_ns > previous.source_timestamp_ns
                    oi_change = sample.oi - previous.oi if new_option_tick and sample.oi is not None and previous.oi is not None else None
                    ltp_change = sample.ltp - previous.ltp if new_option_tick and sample.ltp is not None and previous.ltp is not None else None
                    iv_change = sample.iv - previous.iv if sample.iv is not None and previous.iv is not None and sample.iv != previous.iv else None
                    changes[token] = {
                        "interval_seconds": round(elapsed_ns / 1_000_000_000, 2),
                        "oi_change": oi_change,
                        "oi_change_pct": round(oi_change * 100.0 / previous.oi, 4) if oi_change is not None and previous.oi not in (None, 0) else None,
                        "iv_change": round(iv_change, 4) if iv_change is not None else None,
                        "ltp_change": round(ltp_change, 4) if ltp_change is not None else None,
                        "ltp_change_pct": round(ltp_change * 100.0 / previous.ltp, 4) if ltp_change is not None and previous.ltp not in (None, 0) else None,
                    }
            self._one_second_changes = changes
            self._previous_cycle = updates.copy()
            self._latest = updates
            cutoff_ns = sampled_at - self.history_limit * 1_000_000_000
            for token, sample in pending_history:
                history = self._history[token]
                if not history or history[-1].source_timestamp_ns != sample.source_timestamp_ns:
                    history.append(sample)
                while history and history[0].source_timestamp_ns < cutoff_ns:
                    history.popleft()
                if persistence_ok:
                    self._last_source_timestamp[token] = sample.source_timestamp_ns
            self._last_sampled_at_ns = sampled_at
            self._last_error = None if updates else "no fresh option quotes available"
        return self.snapshot()

    @property
    def configured_expiry(self) -> str | None:
        universe = getattr(self.quote_source, "_universe", None)
        return None if universe is None else universe.expiry

    def inherit_history(self, previous: "IVOneSecondSampler | None", universe: Any) -> int:
        """Carry recent OI ticks only for identical contracts in the same expiry."""
        if previous is None or previous is self:
            return 0
        if (
            previous.config.symbol != self.config.symbol
            or previous.configured_expiry != universe.expiry
            or self.config.symbol != universe.underlying
        ):
            return 0
        valid = {
            contract.token: (contract.strike, contract.option_type)
            for contract in universe.contracts
        }
        with previous._lock:
            prior = {
                token: tuple(rows)
                for token, rows in previous._history.items()
                if token in valid
            }
        now_ns = self.clock_ns()
        cutoff_ns = now_ns - self.history_limit * 1_000_000_000
        copied = 0
        with self._lock:
            for token, rows in prior.items():
                strike, option_type = valid[token]
                eligible = [
                    item for item in rows
                    if item.underlying == self.config.symbol
                    and item.expiry == universe.expiry
                    and item.strike == strike
                    and item.option_type == option_type
                    and item.source_timestamp_ns >= cutoff_ns
                    and item.source_timestamp_ns <= now_ns
                    and item.oi is not None
                ]
                if not eligible:
                    continue
                history = self._history[token]
                known = {item.source_timestamp_ns for item in history}
                for item in eligible:
                    if item.source_timestamp_ns not in known:
                        history.append(item)
                        known.add(item.source_timestamp_ns)
                        copied += 1
                if history:
                    ordered = sorted(history, key=lambda item: item.source_timestamp_ns)
                    history.clear()
                    history.extend(ordered)
                    while history and history[0].source_timestamp_ns < cutoff_ns:
                        history.popleft()
                    self._last_source_timestamp[token] = history[-1].source_timestamp_ns
        return copied

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
            changes = dict(self._one_second_changes)
        live_metrics = [
            {
                "token": item.token,
                "underlying": item.underlying,
                "expiry": item.expiry,
                "strike": item.strike,
                "option_type": item.option_type,
                "oi": item.oi,
                "oi_live": item.oi is not None,
                "iv": item.iv,
                "iv_unit": "PERCENT",
                "iv_live": item.iv is not None,
                "iv_input": item.iv_input,
                "mark": item.mark,
                "ltp": item.ltp,
                "source_timestamp_ns": item.source_timestamp_ns,
                "spot_timestamp_ns": item.spot_timestamp_ns,
                "source_age_ms": round(item.source_age_ms, 2),
                "sampled_at_ns": item.sampled_at_ns,
                "change_interval_seconds": changes.get(item.token, {}).get("interval_seconds"),
                "oi_change_1s": changes.get(item.token, {}).get("oi_change"),
                "oi_change_pct_1s": changes.get(item.token, {}).get("oi_change_pct"),
                "iv_change_1s": changes.get(item.token, {}).get("iv_change"),
                "ltp_change_1s": changes.get(item.token, {}).get("ltp_change"),
                "ltp_change_pct_1s": changes.get(item.token, {}).get("ltp_change_pct"),
            }
            for item in sorted(samples, key=lambda row: (row.strike, row.option_type))
        ]
        return {
            "cadence_seconds": 1,
            "sampled_at_ns": sampled_at,
            "sample_count": len(samples),
            "oi_live_count": sum(item.oi is not None for item in samples),
            "iv_live_count": sum(item.iv is not None for item in samples),
            "oi_values": {item.token: item.oi for item in samples},
            "iv_values": {item.token: item.iv for item in samples},
            "live_metrics": live_metrics,
            "samples": samples,
            "history_samples_by_token": history_sizes,
            "history_persistence": "ENABLED" if self.history_session_factory is not None else "DISABLED",
            "history_persisted_count": self._history_persisted_count,
            "history_persistence_error": self._history_persistence_error,
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
                    self._latest.clear()
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
