"""Independent live index-spot consumer for the IV Skew / IV Flip scanner.

Uses the process-wide CommonStrategyMarketFeed; it never owns a broker socket
and never borrows Box Spread's tracker or subscription lifecycle.
"""
from __future__ import annotations

from math import isfinite
from threading import RLock
from time import time_ns
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Callable, Iterable

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentType
from app.market_data.registry import InstrumentDescriptor
from app.core.database import SessionLocal
from app.models.iv_spot_last_price import IVSpotLastPrice
from app.models.iv_spot_observation import IVSpotObservation

from .sampler import SpotSnapshot


class IVSpotFeedAdapter:
    CONSUMER = "iv-skew-flip-spot"
    DEFAULT_SYMBOLS = ("NIFTY", "BANKNIFTY")

    def __init__(
        self,
        *,
        feed: Any | None = None,
        clock_ns: Callable[[], int] = time_ns,
        max_spot_age_seconds: float = 3.0,
        persist_last_price: bool | None = None,
    ) -> None:
        if max_spot_age_seconds <= 0:
            raise ValueError("max_spot_age_seconds must be positive")
        self._persist_last_price = (feed is None) if persist_last_price is None else bool(persist_last_price)
        self.feed = feed or CommonStrategyMarketFeed(self.CONSUMER, mode=1)
        self.clock_ns = clock_ns
        self.max_spot_age_ns = int(max_spot_age_seconds * 1_000_000_000)
        self._lock = RLock()
        self._keys: dict[str, Any] = {}
        self._latest: dict[str, SpotSnapshot] = {}
        # Consumer-local validation telemetry distinguishes a callback that
        # returned normally from a market-data record actually accepted here.
        self._record_callbacks_received = 0
        self._accepted_records = 0
        self._rejected_records: dict[str, int] = {}
        self._started = False
        if self._persist_last_price:
            self._restore_last_prices()

    def _restore_last_prices(self) -> None:
        """Restore durable last prices; persistence failures never block live feed."""
        try:
            with SessionLocal() as db:
                rows = db.query(IVSpotLastPrice).all()
            with self._lock:
                self._latest.update({
                    row.symbol: SpotSnapshot(float(row.price), int(row.timestamp_ns), int(row.timestamp_ns))
                    for row in rows
                    if row.symbol in self.DEFAULT_SYMBOLS
                    and isfinite(float(row.price)) and float(row.price) > 0
                    and int(row.timestamp_ns) > 0
                })
        except Exception:
            # A database outage must not prevent the live IV market-data path.
            return

    @staticmethod
    def _descriptors(instrument_master: Any, symbols: Iterable[str]) -> tuple[InstrumentDescriptor, ...]:
        descriptors = []
        seen_tokens: set[str] = set()
        for raw_symbol in symbols:
            symbol = str(raw_symbol or "").strip().upper()
            if symbol not in IVSpotFeedAdapter.DEFAULT_SYMBOLS:
                raise ValueError(f"unsupported IV scanner spot symbol: {symbol}")
            row = instrument_master.resolve_index_instrument(symbol, "NSE")
            token = str(row.get("token", "")).strip()
            if not token or token in seen_tokens:
                raise LookupError(f"missing or duplicate NSE index token for {symbol}")
            seen_tokens.add(token)
            trading_symbol = str(row.get("symbol") or row.get("name") or symbol).strip()
            descriptors.append(CommonStrategyMarketFeed.descriptor(
                exchange="NSE",
                segment="NSE",
                token=token,
                symbol=trading_symbol,
                instrument_type=InstrumentType.INDEX.value,
                underlying=symbol,
            ))
        if not descriptors:
            raise ValueError("at least one IV scanner spot symbol is required")
        return tuple(descriptors)

    def start(self, instrument_master: Any, symbols: Iterable[str] = DEFAULT_SYMBOLS) -> dict[str, Any]:
        descriptors = self._descriptors(instrument_master, symbols)
        # This clears only the IV spot consumer's previous registration.
        self.feed.stop()
        with self._lock:
            self._keys = {
                str(item.underlying).upper(): item.key
                for item in descriptors
            }
        keys = self.feed.start(descriptors, self._on_record)
        self._started = True
        return {
            "status": "SUBSCRIBED",
            "consumer": self.CONSUMER,
            "symbols": sorted(self._keys),
            "contract_count": len(keys),
        }

    @staticmethod
    def _has_source_timestamp(record: Any) -> bool:
        payload = getattr(record, "payload", None)
        if not isinstance(payload, dict):
            return False
        for name in (
            "exchange_timestamp_ns", "timestamp_ns", "exchange_timestamp",
            "exchange_timestamp_ms", "timestamp", "feed_time",
        ):
            value = payload.get(name)
            if value is None or isinstance(value, bool):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if isfinite(number) and number > 0:
                return True
        return False

    def _reject_record(self, reason: str) -> None:
        with self._lock:
            self._rejected_records[reason] = self._rejected_records.get(reason, 0) + 1

    def _on_record(self, record: Any) -> None:
        with self._lock:
            self._record_callbacks_received += 1
        instrument = getattr(record, "instrument", None)
        timestamp_ns = getattr(record, "timestamp_ns", None)
        price = getattr(record, "ltp", None)
        if not self._has_source_timestamp(record):
            self._reject_record("missing_or_invalid_provider_timestamp")
            return
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns <= 0:
            self._reject_record("invalid_normalized_timestamp")
            return
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not isfinite(float(price)) or price <= 0:
            self._reject_record("invalid_or_missing_price")
            return
        received_at_ns = self.clock_ns()
        with self._lock:
            # A broker token may already be registered by another shared-feed
            # consumer with generic/different instrument metadata. The exact
            # NSE token subscribed by this adapter is authoritative for routing.
            symbol = next((name for name, key in self._keys.items() if key == instrument), None)
            if symbol is None:
                self._reject_record("unmatched_instrument")
                return
            previous = self._latest.get(symbol)
            if previous is not None and timestamp_ns < previous.timestamp_ns:
                self._reject_record("out_of_order_source_timestamp")
                return
            # Exchange timestamps describe the source observation; receive time
            # describes whether the transport is delivering current frames.
            # Keep both clocks separate: brokers may repeat an index timestamp
            # across quote frames, which must refresh feed health without
            # fabricating a new exchange observation.
            snapshot = SpotSnapshot(float(price), timestamp_ns, received_at_ns)
            self._latest[symbol] = snapshot
            self._accepted_records += 1
        if not self._persist_last_price:
            return
        try:
            with SessionLocal() as db:
                db.merge(IVSpotLastPrice(symbol=symbol, price=snapshot.price, timestamp_ns=snapshot.timestamp_ns))
                db.merge(IVSpotObservation(
                    underlying=symbol,
                    source_timestamp_ns=snapshot.timestamp_ns,
                    price=snapshot.price,
                    received_at_ns=received_at_ns,
                ))
                db.commit()
        except Exception:
            # Keep the in-memory live quote even if durable storage is unavailable.
            return

    def spot_snapshot(self, symbol: str, *, now_ns: int | None = None) -> SpotSnapshot | None:
        current = self.clock_ns() if now_ns is None else now_ns
        with self._lock:
            snapshot = self._latest.get(str(symbol or "").strip().upper())
        if snapshot is None:
            return None
        receive_age = current - (snapshot.received_at_ns or snapshot.timestamp_ns)
        source_age = current - snapshot.timestamp_ns
        # A fresh transport frame cannot make an old market observation fresh.
        # Equal exchange timestamps remain acceptable only inside the same
        # strict source-age window (e.g. provider timestamp granularity).
        if (receive_age < 0 or receive_age > self.max_spot_age_ns
                or source_age < 0 or source_age > self.max_spot_age_ns):
            return None
        return snapshot

    def snapshot(self, *, now_ns: int | None = None) -> dict[str, Any]:
        current = self.clock_ns() if now_ns is None else now_ns
        with self._lock:
            expected = tuple(self._keys)
            expected_keys = dict(self._keys)
            latest = dict(self._latest)
            started = self._started
        fresh = {
            symbol: value for symbol, value in latest.items()
            if 0 <= current - (value.received_at_ns or value.timestamp_ns) <= self.max_spot_age_ns
            and 0 <= current - value.timestamp_ns <= self.max_spot_age_ns
        }
        missing = sorted(set(expected) - set(fresh))
        if not started:
            status = "STOPPED"
        elif not missing:
            status = "LIVE"
        elif fresh:
            status = "PARTIAL"
        elif latest:
            status = "STALE"
        else:
            status = "WAITING_FOR_TICKS"
        # Keep transport/routing health separate from per-symbol market freshness.
        # CommonStrategyMarketFeed is shared process-wide, so these counters are
        # explicitly labelled as manager-wide rather than falsely attributed to
        # this consumer alone.
        transport: dict[str, Any] = {}
        feed_snapshot = getattr(self.feed, "snapshot", None)
        if callable(feed_snapshot):
            try:
                shared = feed_snapshot()
                if isinstance(shared, dict):
                    transport_fields = (
                        "subscriptions", "active_instruments", "socket_groups",
                        "required_socket_groups", "missing_socket_groups",
                        "unconnected_required_groups", "socket_health_by_group",
                        "raw_frames_received", "ticks_received", "ticks_by_exchange_type",
                        "unmatched_frames", "exchange_mismatch_frames",
                        "missing_exchange_type_frames", "ambiguous_token_frames",
                        "normalized_records_emitted", "normalized_records_delivered",
                        "normalizer_errors", "last_normalizer_error",
                        "delivery_errors", "normalized_delivery_errors",
                        "last_delivery_error", "last_raw_frame_age_seconds",
                        "last_raw_frame_age_seconds_by_group",
                        "last_successful_route_age_seconds_by_group",
                        "last_successful_delivery_age_seconds_by_group",
                        "last_exchange_tick_age_seconds_by_exchange_type",
                        "last_exchange_tick_received_age_seconds_by_exchange_type",
                        "last_raw_frame_age_seconds_by_exchange_group",
                    )
                    transport = {
                        "scope": "shared_manager_process_wide",
                        **{key: shared[key] for key in transport_fields if key in shared},
                    }
            except Exception as exc:
                transport = {
                    "scope": "shared_manager_process_wide",
                    "diagnostics_error": f"{type(exc).__name__}: {exc}",
                }
        # Keep the last broker-traded spot visible after the freshness window
        # expires (including after market close). Signal eligibility continues
        # to depend exclusively on fresh_symbols / spot_snapshot().
        return {
            "status": status,
            "consumer": self.CONSUMER,
            "expected_symbols": sorted(expected),
            "expected_instruments": {
                symbol: {
                    "exchange": key.exchange,
                    "segment": key.segment,
                    "token": key.token,
                }
                for symbol, key in sorted(expected_keys.items())
            },
            "transport": transport,
            "record_validation": {
                "scope": "iv_spot_consumer",
                "callbacks_received": self._record_callbacks_received,
                "accepted_records": self._accepted_records,
                "rejected_records": sum(self._rejected_records.values()),
                "rejected_by_reason": dict(self._rejected_records),
            },
            "fresh_symbols": sorted(fresh),
            "missing_symbols": missing,
            "spot": {
                symbol: {
                    "price": item.price,
                    "last_price": item.price,
                    "timestamp_ns": item.timestamp_ns,
                    "last_trade_time": datetime.fromtimestamp(
                        item.timestamp_ns / 1_000_000_000, tz=timezone.utc
                    ).astimezone(ZoneInfo("Asia/Kolkata")).isoformat(),
                    "age_ms": round((current - (item.received_at_ns or item.timestamp_ns)) / 1_000_000, 2),
                    "source_age_ms": round((current - item.timestamp_ns) / 1_000_000, 2),
                    "received_at_ns": item.received_at_ns or item.timestamp_ns,
                    "fresh": symbol in fresh,
                    "quote_status": "LIVE" if symbol in fresh else "LAST_KNOWN_STALE",
                }
                for symbol, item in latest.items()
            },
            "signals_allowed": bool(expected) and not missing,
        }

    def stop(self) -> None:
        self.feed.stop()
        with self._lock:
            self._started = False
            # Last prices are intentionally retained across runner OFF/ON cycles.
            self._keys.clear()


__all__ = ["IVSpotFeedAdapter"]
