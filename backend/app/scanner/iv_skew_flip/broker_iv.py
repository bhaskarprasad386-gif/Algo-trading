"""Bounded Angel One broker-native IV cache for the live IV signal path.

REST work runs only on this dedicated worker. Shared WebSocket callbacks never
call this module. Greeks timestamps are local response-capture times because
Angel One's Option Greeks response has no exchange event timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event, RLock, Thread
from time import monotonic, time_ns
from typing import Any, Callable

from app.market_data.client import MarketDataClient
from .angelone_option_snapshot import _greek_key, _number, _rows


@dataclass(frozen=True)
class BrokerIVValue:
    underlying: str
    expiry: str
    strike: float
    option_type: str
    token: str
    value: float
    captured_at_ns: int
    source: str = "ANGEL_ONE"


class AngelOneBrokerIVCache:
    """One REST worker, one request cadence per selected index/expiry."""

    def __init__(
        self, *, client: Any | None = None, interval_seconds: float = 30.0,
        max_age_seconds: float = 90.0, clock_ns: Callable[[], int] = time_ns,
    ) -> None:
        if interval_seconds < 15.0 or interval_seconds > 120.0:
            raise ValueError("broker IV cadence must be between 15 and 120 seconds")
        if max_age_seconds < interval_seconds or max_age_seconds > 180.0:
            raise ValueError("broker IV freshness must cover cadence and remain <=180 seconds")
        self.client = client or MarketDataClient()
        self.interval_seconds = float(interval_seconds)
        self.max_age_ns = int(max_age_seconds * 1_000_000_000)
        self.clock_ns = clock_ns
        self._lock = RLock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._universes: dict[str, Any] = {}
        self._cache: dict[tuple[str, str, float, str, str], BrokerIVValue] = {}
        self._last_fetch: dict[tuple[str, str], float] = {}
        self._status: dict[str, dict[str, Any]] = {}

    def set_universe(self, stream_id: str, universe: Any) -> None:
        if universe is None or not getattr(universe, "contracts", None):
            raise ValueError("resolved option universe is required")
        key = str(stream_id).strip().upper()
        if key not in {"NIFTY_WEEKLY", "BANKNIFTY_MONTHLY"}:
            raise ValueError("only NIFTY Weekly and BANKNIFTY Monthly IV universes are supported")
        with self._lock:
            self._universes[key] = universe
            # Expiry rotation invalidates the previous expiry immediately.
            self._cache = {
                cache_key: value for cache_key, value in self._cache.items()
                if not (value.underlying == universe.underlying and value.expiry != universe.expiry)
            }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name="iv-angelone-greeks-rest", daemon=True)
            self._thread.start()

    def _fetch(self, stream_id: str, universe: Any) -> None:
        key = (str(universe.underlying).upper(), str(universe.expiry))
        try:
            from datetime import datetime
            expiry = datetime.strptime(str(universe.expiry), "%Y-%m-%d").strftime("%d%b%Y").upper()
            response = self.client.option_greeks(universe.underlying, expiry)
            rows = _rows(response.get("data") if isinstance(response, dict) else None)
            expected = {
                (round(float(c.strike), 4), str(c.option_type).upper()): c
                for c in universe.contracts
            }
            staged: dict[tuple[str, str, float, str, str], BrokerIVValue] = {}
            seen: set[tuple[float, str]] = set()
            captured = self.clock_ns()
            for row in rows:
                identity = _greek_key(row, str(universe.expiry))
                if identity is None or identity not in expected or identity in seen:
                    continue
                seen.add(identity)
                raw_iv = _number(row.get("impliedVolatility"))
                # Angel One reports IV in percentage points (e.g. 14.25).
                if raw_iv is None or not 0.0 < raw_iv <= 500.0:
                    continue
                contract = expected[identity]
                value = BrokerIVValue(
                    underlying=str(universe.underlying).upper(),
                    expiry=str(universe.expiry),
                    strike=float(contract.strike),
                    option_type=str(contract.option_type).upper(),
                    token=str(contract.token),
                    value=raw_iv / 100.0,
                    captured_at_ns=captured,
                )
                cache_key = (value.underlying, value.expiry, value.strike, value.option_type, value.token)
                staged[cache_key] = value
            with self._lock:
                current = self._universes.get(stream_id)
                if (
                    self._stop.is_set() or current is None
                    or str(current.underlying).upper() != key[0]
                    or str(current.expiry) != key[1]
                ):
                    return
                # Replace the current expiry snapshot atomically. Never retain
                # missing contracts from a prior REST response as current data.
                self._cache = {
                    cache_key: value for cache_key, value in self._cache.items()
                    if not (value.underlying == key[0] and value.expiry == key[1])
                }
                self._cache.update(staged)
                self._status[stream_id] = {
                    "status": "LIVE" if len(staged) == len(expected) else "PARTIAL",
                    "source": "ANGEL_ONE",
                    "captured_at_ns": captured,
                    "expected_contracts": len(expected),
                    "fresh_contracts": len(staged),
                    "missing_contracts": len(expected) - len(staged),
                    "error": None,
                }
        except Exception as exc:
            with self._lock:
                self._status[stream_id] = {
                    "status": "ERROR", "source": "ANGEL_ONE",
                    "expected_contracts": len(universe.contracts),
                    "fresh_contracts": 0, "error": type(exc).__name__,
                }

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                universes = tuple(self._universes.items())
            now = monotonic()
            for stream_id, universe in universes:
                if self._stop.is_set():
                    break
                key = (str(universe.underlying).upper(), str(universe.expiry))
                with self._lock:
                    due = now - self._last_fetch.get(key, 0.0) >= self.interval_seconds
                    if due:
                        # Reserve before network I/O: one worker means one
                        # in-flight request per key and no overlapping retries.
                        self._last_fetch[key] = now
                if due:
                    self._fetch(stream_id, universe)
            self._stop.wait(1.0)

    def enrich(self, stream_id: str, quotes: tuple[Any, ...], now_ns: int | None = None) -> tuple[Any, ...] | None:
        from dataclasses import replace
        now = self.clock_ns() if now_ns is None else now_ns
        with self._lock:
            universe = self._universes.get(str(stream_id).upper())
            cache = dict(self._cache)
        if universe is None:
            return None
        expected = {
            (str(universe.underlying).upper(), str(universe.expiry), float(c.strike),
             str(c.option_type).upper(), str(c.token))
            for c in universe.contracts
        }
        enriched = []
        for quote in quotes:
            key = (quote.underlying.upper(), quote.expiry, float(quote.strike),
                   quote.option_type.upper(), str(quote.token or ""))
            item = cache.get(key)
            if item is None or now - item.captured_at_ns < 0 or now - item.captured_at_ns > self.max_age_ns:
                return None
            enriched.append(replace(
                quote, broker_iv=item.value, iv_source=item.source,
                iv_timestamp_ns=item.captured_at_ns,
            ))
        if len(enriched) != len(expected) or {
            (q.underlying.upper(), q.expiry, float(q.strike), q.option_type.upper(), str(q.token or ""))
            for q in enriched
        } != expected:
            return None
        return tuple(enriched)

    def snapshot(self) -> dict[str, Any]:
        now = self.clock_ns()
        with self._lock:
            statuses = {key: dict(value) for key, value in self._status.items()}
            cache = tuple(self._cache.values())
            alive = self._thread is not None and self._thread.is_alive()
        fresh = sum(0 <= now - item.captured_at_ns <= self.max_age_ns for item in cache)
        return {
            "status": "RUNNING" if alive else "STOPPED",
            "source": "ANGEL_ONE_OPTION_GREEKS",
            "cadence_seconds": self.interval_seconds,
            "max_age_seconds": self.max_age_ns / 1_000_000_000,
            "fresh_cached_contracts": fresh,
            "chains": statuses,
            "rest_calls_from_websocket_callback": 0,
        }

    def stop(self, timeout_seconds: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout_seconds))
        if thread is not None and thread.is_alive():
            raise RuntimeError("Angel One broker-IV worker did not stop within deadline")
        with self._lock:
            self._thread = None
            self._cache.clear()
            self._universes.clear()
            self._last_fetch.clear()
