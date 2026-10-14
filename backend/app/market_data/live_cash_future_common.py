"""Live Cash-Future runner backed by the common WebSocket/normalization layer."""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Any, Callable, Mapping

from app.core.config import settings
from app.core.logger import app_logger
from app.market_data.cash_future_opportunity import CashFutureOpportunityScanner, CashFutureScanResult
from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.common_strategy_feed import shared_common_manager
from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.daily_shard_catalog import DailyMarketDataShardCatalog
from app.market_data.historical import HistoricalDataClient
from app.market_data.ingestion import BoundedMarketDataIngestor
from app.market_data.persistence import DailySQLiteMarketDataRepository
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry


def _expiry(value: Any) -> date | None:
    text = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class LiveCashFutureCommonRunner:
    """Subscribe to cash/current/near futures through one common market-data feed."""

    CONSUMER = "cash-future"

    def __init__(
        self,
        data_db: str,
        *,
        instrument_master=None,
        manager: CommonWebSocketManager | None = None,
        scanner: CashFutureOpportunityScanner | None = None,
        on_result: Callable[[CashFutureScanResult], None] | None = None,
        on_payload: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        from app.market_data.instruments import InstrumentMaster
        self.data_db = data_db
        self.instrument_master = instrument_master or InstrumentMaster()
        self.manager = manager or shared_common_manager()
        self.scanner = scanner or CashFutureOpportunityScanner(
            minimum_gap_points=0.0,
            minimum_gross_profit=0.0,
        )
        self.on_result = on_result
        self.on_payload = on_payload
        self.stop_event = threading.Event()
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._repository: DailySQLiteMarketDataRepository | None = None
        self._ingestor: BoundedMarketDataIngestor | None = None
        self._metadata: dict[InstrumentKey, dict[str, Any]] = {}
        self._last_result: CashFutureScanResult | None = None
        self._latest_persisted: dict[InstrumentKey, MarketDataRecord] = {}
        self._last_retention_date: date | None = None
        self._last_contract_refresh_date: date | None = None
        # Runtime path telemetry: proves whether normalized records reach this
        # strategy callback before changing scanner logic.
        self._records_received = 0
        self._records_missing_metadata = 0
        self._payload_callbacks = 0
        self._scanner_updates = 0
        self._scanner_results = 0
        self._records_missing_executable_quote = 0
        self._scanner_rejections = 0
        # Bounded, in-memory comparison of broker depth shape vs canonical quotes.
        # Never retain or log complete broker frames.
        self._depth_diagnostic_lock = threading.Lock()
        self._depth_diagnostics: dict[str, dict[str, Any]] = {}
        self._depth_diagnostic_leg_counts = {"CASH": 0, "FUTURE": 0}
        self._depth_diagnostic_limit_per_leg = 6

    @property
    def last_result(self) -> CashFutureScanResult | None:
        return self._last_result

    def snapshot(self) -> dict[str, Any]:
        """Return live runner state without exposing broker/socket internals."""
        ingestor = self._ingestor
        return {
            "running": not self.stop_event.is_set() and not self._stopped,
            "registered_instruments": len(self._metadata),
            "ingestor": None if ingestor is None else ingestor.snapshot(),
            "records_received": self._records_received,
            "records_missing_metadata": self._records_missing_metadata,
            "payload_callbacks": self._payload_callbacks,
            "scanner_updates": self._scanner_updates,
            "scanner_results": self._scanner_results,
            "records_missing_executable_quote": self._records_missing_executable_quote,
            "scanner_rejections": self._scanner_rejections,
            "scanner_out_of_order_records": getattr(self.scanner, "out_of_order_records", 0),
            "scanner_stale_pair_rejections": getattr(self.scanner, "stale_pair_rejections", 0),
            "depth_diagnostic": self._depth_diagnostic_snapshot(),
        }

    def _build_descriptors(self) -> tuple[InstrumentDescriptor, ...]:
        rows = self.instrument_master.download()
        configured = {x.strip().upper() for x in settings.LIVE_CASH_FUTURE_SYMBOLS.split(",") if x.strip()}
        universe = configured or None
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        futures: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if str(row.get("exch_seg", "")).upper() != "NFO":
                continue
            if str(row.get("instrumenttype", "")).upper() != "FUTSTK":
                continue
            underlying = str(row.get("name") or "").strip().upper()
            if not underlying or (universe is not None and underlying not in universe):
                continue
            expiry = _expiry(row.get("expiry"))
            token = str(row.get("token") or "").strip()
            symbol = str(row.get("symbol") or "").strip().upper()
            if not token or not symbol or expiry is None or expiry < today:
                continue
            key = (underlying, token)
            if key in seen:
                continue
            seen.add(key)
            futures.append({**row, "_underlying": underlying, "_expiry": expiry})
        futures.sort(key=lambda x: (x["_underlying"], x["_expiry"], str(x.get("symbol"))))
        selected: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for row in futures:
            underlying = row["_underlying"]
            if counts.get(underlying, 0) >= 2:
                continue
            try:
                cash = self.instrument_master.resolve_cash_instrument(underlying, "NSE")
            except (LookupError, ValueError):
                continue
            item = dict(row)
            item["_cash"] = cash
            item["_month"] = "CURRENT" if counts.get(underlying, 0) == 0 else "NEAR"
            selected.append(item)
            counts[underlying] = counts.get(underlying, 0) + 1

        descriptors: list[InstrumentDescriptor] = []
        metadata: dict[InstrumentKey, dict[str, Any]] = {}
        registered_cash: set[InstrumentKey] = set()
        for row in selected:
            underlying = row["_underlying"]
            cash = row["_cash"]
            cash_key = InstrumentKey("NSE", "NSE", str(cash["token"]))
            future_key = InstrumentKey("NFO", "NFO", str(row["token"]))
            cash_symbol = str(cash.get("symbol") or f"{underlying}-EQ")
            lot = int(row.get("lotsize") or row.get("lotSize") or 0) or None
            if cash_key not in registered_cash:
                descriptors.append(InstrumentDescriptor(
                    key=cash_key, symbol=cash_symbol, instrument_type="equity",
                    exchange="NSE", segment="NSE", lot_size=None,
                ))
                registered_cash.add(cash_key)
            descriptors.append(InstrumentDescriptor(
                key=future_key, symbol=str(row["symbol"]), instrument_type="future",
                exchange="NFO", segment="NFO", expiry=row["_expiry"].isoformat(),
                lot_size=lot,
            ))
            metadata[cash_key] = {
                "leg": "CASH", "underlying": underlying, "contract_month": "CASH",
            }
            metadata[future_key] = {
                "leg": "FUTURE", "underlying": underlying, "contract_month": row["_month"],
            }
        self._metadata = metadata
        return tuple(descriptors)

    def _roll_contracts_if_needed(self) -> None:
        """Refresh CURRENT/NEAR contracts once per IST date without dropping shared feed state."""
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        if self._last_contract_refresh_date == today:
            return
        previous_metadata = dict(self._metadata)
        try:
            descriptors = self._build_descriptors()
            if not descriptors:
                self._metadata = previous_metadata
                app_logger.warning("Cash-Future contract rollover found no eligible contracts for %s", today.isoformat())
                return
            accepted = self._register_descriptors(descriptors)
            new_metadata = {key: meta for key, meta in self._metadata.items() if key in accepted}
            if not new_metadata:
                self._metadata = previous_metadata
                app_logger.warning("Cash-Future contract rollover found no usable descriptors for %s", today.isoformat())
                return
            old_keys = set(previous_metadata)
            new_keys = set(new_metadata)
            added_keys = sorted(new_keys - old_keys, key=lambda key: key.value)
            removed_keys = sorted(old_keys - new_keys, key=lambda key: key.value)
            if added_keys:
                self.manager.subscribe(self.CONSUMER, added_keys, mode=3)
            if removed_keys:
                self.manager.unsubscribe(self.CONSUMER, removed_keys)
            self._metadata = new_metadata
            reset_scanner = getattr(self.scanner, "reset", None)
            if callable(reset_scanner):
                reset_scanner()
            self._last_contract_refresh_date = today
            app_logger.info(
                "Cash-Future contract rollover complete date=%s old=%s new=%s added=%s removed=%s",
                today.isoformat(), len(old_keys), len(new_keys), len(added_keys), len(removed_keys),
            )
        except Exception as exc:
            self._metadata = previous_metadata
            app_logger.warning("Cash-Future contract rollover deferred date=%s error=%s", today.isoformat(), exc)

    @staticmethod
    def _depth_shape(value: Any) -> dict[str, Any] | None:
        """Describe depth structure without retaining broker prices or quantities."""
        if isinstance(value, Mapping):
            return {"type": "mapping", "keys": sorted(str(key) for key in value.keys())[:12]}
        if isinstance(value, list):
            first = value[0] if value else None
            return {
                "type": "list",
                "length": len(value),
                "first_item_type": type(first).__name__ if first is not None else None,
                "first_item_keys": sorted(str(key) for key in first.keys())[:12]
                if isinstance(first, Mapping) else [],
            }
        return {"type": type(value).__name__} if value is not None else None

    def _on_raw_tick(self, payload: dict[str, Any]) -> None:
        """Keep only bounded field/shape metadata for comparing raw and normalized ticks."""
        if not isinstance(payload, dict):
            return
        token = str(payload.get("token") or payload.get("symboltoken") or "").strip()
        try:
            exchange_type = int(payload.get("exchange_type", payload.get("exchangeType")))
        except (TypeError, ValueError, OverflowError):
            return
        exchange_identity = {1: ("NSE", "NSE"), 2: ("NFO", "NFO")}.get(exchange_type)
        if not token or exchange_identity is None:
            return
        key = InstrumentKey(exchange_identity[0], exchange_identity[1], token)
        meta = self._metadata.get(key)
        if meta is None:
            return
        identity = key.value
        depth_names = (
            "best_5_buy_data", "best_buy_data", "best_buy", "buy",
            "best_5_sell_data", "best_sell_data", "best_sell", "sell",
            "buy_price", "sell_price", "buy_quantity", "sell_quantity",
        )
        with self._depth_diagnostic_lock:
            sample = self._depth_diagnostics.get(identity)
            if sample is None:
                leg = meta["leg"]
                if self._depth_diagnostic_leg_counts.get(leg, 0) >= self._depth_diagnostic_limit_per_leg:
                    return
                self._depth_diagnostic_leg_counts[leg] = self._depth_diagnostic_leg_counts.get(leg, 0) + 1
                sample = {
                    "instrument": identity,
                    "symbol": str(meta["underlying"]),
                    "leg": leg,
                    "raw_top_level_keys": [],
                    "raw_depth_shapes": {},
                    "normalized": None,
                }
                self._depth_diagnostics[identity] = sample
            sample["raw_top_level_keys"] = sorted(str(key) for key in payload.keys())[:40]
            sample["raw_depth_shapes"] = {
                name: self._depth_shape(payload.get(name))
                for name in depth_names if name in payload
            }
            # Presence-only summary for direct aliases; values are deliberately omitted.
            sample["direct_quote_fields_present"] = [
                name for name in ("buy_price", "sell_price", "buy_quantity", "sell_quantity")
                if payload.get(name) is not None
            ]

    def _update_depth_diagnostic_record(self, record: MarketDataRecord) -> None:
        identity = record.instrument.value
        with self._depth_diagnostic_lock:
            sample = self._depth_diagnostics.get(identity)
            if sample is not None:
                sample["normalized"] = {
                    "ltp_present": record.ltp is not None,
                    "bid": record.bid,
                    "ask": record.ask,
                    "bid_qty_present": record.bid_qty is not None,
                    "ask_qty_present": record.ask_qty is not None,
                }

    def _depth_diagnostic_snapshot(self) -> dict[str, Any]:
        with self._depth_diagnostic_lock:
            return {
                "storage": "bounded in-memory only; no raw values persisted",
                "sample_limit_per_leg": self._depth_diagnostic_limit_per_leg,
                "sampled_instruments": len(self._depth_diagnostics),
                "samples": [dict(sample) for sample in self._depth_diagnostics.values()],
            }

    def _record_payload(self, record: MarketDataRecord, meta: dict[str, Any]) -> dict[str, Any]:
        payload = record.as_dict()
        payload.update({
            "source_timestamp_ns": record.timestamp_ns,
            "leg": meta["leg"],
            "underlying": meta["underlying"],
            "contract_month": meta["contract_month"],
            "received_at_ns": time.time_ns(),
        })
        return payload

    def _persist_record(self, record: MarketDataRecord, meta: dict[str, Any]) -> None:
        if self._ingestor is None:
            return
        payload = self._record_payload(record, meta)
        from app.backtesting.historical_catalog import HistoricalRecord
        try:
            self._ingestor.submit_historical(
                HistoricalRecord(
                    source="angelone-live-1s",
                    instrument=record.instrument.value,
                    timeframe="1s",
                    timestamp_ns=record.timestamp_ns,
                    payload=payload,
                )
            )
        except Exception as exc:
            app_logger.error("Cash-Future common persistence submit failed: %s", exc)

    def _on_record(self, record: MarketDataRecord) -> None:
        self._update_depth_diagnostic_record(record)
        self._records_received += 1
        meta = self._metadata.get(record.instrument)
        if meta is None:
            self._records_missing_metadata += 1
            return
        # The broker tick usually contains no underlying field. The runner's
        # instrument-master mapping is authoritative and must be attached before
        # the scanner pairs cash symbols with their current/near futures.
        if record.underlying != meta["underlying"]:
            record = replace(record, underlying=meta["underlying"])
        if settings.LIVE_MARKET_DATA_PERSISTENCE_ENABLED and self._ingestor is not None:
            # Persist one latest broker tick per second. The canonical WebSocket
        # can emit many ticks inside a second, but this stream is explicitly 1s.
            previous = self._latest_persisted.get(record.instrument)
            second = (record.timestamp_ns // 1_000_000_000) * 1_000_000_000
            if previous is not None:
                previous_second = (previous.timestamp_ns // 1_000_000_000) * 1_000_000_000
                if second == previous_second:
                    self._latest_persisted[record.instrument] = record
                else:
                    self._persist_record(previous, self._metadata[record.instrument])
                    self._latest_persisted[record.instrument] = record
            else:
                self._latest_persisted[record.instrument] = record
        if self.on_payload is not None:
            try:
                self.on_payload(self._record_payload(record, meta))
                self._payload_callbacks += 1
            except Exception as exc:
                app_logger.error("Cash-Future payload callback failed: %s", exc)
        try:
            self._scanner_updates += 1
            result = self.scanner.update(
                record,
                contract_month=meta["contract_month"],
            )
        except ValueError as exc:
            if "two-sided executable quote" in str(exc):
                self._records_missing_executable_quote += 1
            else:
                self._scanner_rejections += 1
            return
        if result is not None and result.signal.qualifies:
            self._scanner_results += 1
            self._last_result = result
            if self.on_result is not None:
                try:
                    self.on_result(result)
                except Exception as exc:
                    app_logger.error("Cash-Future result callback failed: %s", exc)

    def _register_descriptors(self, descriptors: tuple[InstrumentDescriptor, ...]) -> set[InstrumentKey]:
        """Register only canonical descriptors; shared consumers may already own a key."""
        fresh: list[InstrumentDescriptor] = []
        accepted: set[InstrumentKey] = set()
        for descriptor in descriptors:
            existing = self.manager.registry.get(descriptor.key)
            if existing is None:
                fresh.append(descriptor)
                accepted.add(descriptor.key)
                continue
            if existing == descriptor:
                accepted.add(descriptor.key)
                continue
            compatible = (
                existing.instrument_type == descriptor.instrument_type
                and existing.exchange == descriptor.exchange
                and existing.segment == descriptor.segment
            )
            if not compatible:
                app_logger.warning(
                    "Cash-Future skipped conflicting shared descriptor for %s; existing=%s/%s/%s requested=%s/%s/%s",
                    descriptor.key.value, existing.instrument_type, existing.exchange, existing.segment,
                    descriptor.instrument_type, descriptor.exchange, descriptor.segment,
                )
                continue
            accepted.add(descriptor.key)
            app_logger.debug(
                "Cash-Future reusing shared descriptor for %s; canonical symbol=%s",
                descriptor.key.value,
                existing.symbol,
            )
        if fresh:
            self.manager.registry.register_many(tuple(fresh))
        return accepted

    def run_forever(self) -> None:
        if self.stop_event.is_set():
            return
        descriptors = self._build_descriptors()
        if self.stop_event.is_set():
            return
        if not descriptors:
            app_logger.warning("Common Cash-Future runner found no eligible F&O stock contracts")
            return
        accepted = self._register_descriptors(descriptors)
        if not accepted:
            app_logger.warning("Common Cash-Future runner found no usable shared instrument descriptors")
            return
        self._metadata = {key: meta for key, meta in self._metadata.items() if key in accepted}
        self.manager.register_callback(self.CONSUMER, self._on_raw_tick)
        self.manager.register_normalized_callback(self.CONSUMER, self._on_record)
        if settings.LIVE_MARKET_DATA_PERSISTENCE_ENABLED:
            self._repository = DailySQLiteMarketDataRepository(self.data_db)
            self._prune_old_live_shards()
            self._ingestor = BoundedMarketDataIngestor(
                self._repository,
                max_queue=settings.MARKET_DATA_INGEST_QUEUE_MAX,
                batch_size=settings.MARKET_DATA_INGEST_BATCH_SIZE,
                flush_seconds=settings.MARKET_DATA_INGEST_FLUSH_SECONDS,
                put_timeout_seconds=settings.MARKET_DATA_INGEST_PUT_TIMEOUT_SECONDS,
                record_source="angelone-live-1s",
                on_error=lambda exc: app_logger.error("Cash-Future common persistence error: %s", exc),
            )
        else:
            app_logger.info("Cash-Future live market-data persistence disabled; WebSocket/scanner remain live in memory")
        keys = list(self._metadata)
        subscribed = False
        try:
            while not self.stop_event.wait(1.0):
                try:
                    if not subscribed:
                        self.manager.subscribe(self.CONSUMER, keys, mode=3)
                        subscribed = True
                        self._last_contract_refresh_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
                except Exception as exc:
                    # A transient broker/auth/socket failure must not terminate
                    # the shared live runner. Keep the consumer registered and
                    # retry on the next supervision cycle.
                    subscribed = False
                    app_logger.warning("Cash-Future common feed subscribe/retry failed: %s", exc)
                    continue
                try:
                    self._roll_contracts_if_needed()
                    # Keep the scanner independent of a stale broker session. The
                    # common manager preserves the same subscriptions and rebuilds
                    # only sockets that have remained disconnected long enough.
                    self.manager.recover_disconnected(min_age_seconds=10.0)
                except Exception as exc:
                    # Recovery is best-effort; preserve the runner so another
                    # cycle can recreate missing/disconnected common-feed sockets.
                    app_logger.warning("Cash-Future common feed recovery failed: %s", exc)
                if settings.LIVE_MARKET_DATA_PERSISTENCE_ENABLED:
                    self._prune_old_live_shards()
        finally:
            self.stop()

    def _prune_old_live_shards(self) -> None:
        repository = self._repository
        if repository is None or not settings.LIVE_CASH_FUTURE_DAILY_SHARDS_ENABLED:
            return
        current_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        if current_date == self._last_retention_date:
            return
        try:
            removed = repository.prune_shards_older_than(
                retention_days=settings.LIVE_MARKET_DATA_RETENTION_DAYS,
                today=current_date,
            )
            self._last_retention_date = current_date
            if removed:
                app_logger.info(
                    "Cash-Future live shard retention removed %d old shard(s): %s",
                    len(removed), ", ".join(path.name for path in removed),
                )
        except Exception as exc:
            app_logger.error("Cash-Future live shard retention cleanup failed: %s", exc)

    def stop(self) -> None:
        # run_forever() and the asyncio lifespan can both call stop() during
        # cancellation. Serialize cleanup so shared resources are closed once.
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            self.stop_event.set()
            try:
                self.manager.clear_consumer(self.CONSUMER)
            except Exception:
                pass
            if settings.LIVE_MARKET_DATA_PERSISTENCE_ENABLED:
                pending = list(self._latest_persisted.items())
                self._latest_persisted.clear()
                for key, record in pending:
                    meta = self._metadata.get(key)
                    if meta is not None:
                        self._persist_record(record, meta)
            else:
                self._latest_persisted.clear()
            if self._ingestor is not None:
                try:
                    self._ingestor.close()
                except Exception as exc:
                    app_logger.error("Cash-Future common ingestor close failed: %s", exc)
                self._ingestor = None
            if self._repository is not None:
                try:
                    self._repository.close()
                except Exception:
                    pass
                self._repository = None


__all__ = ["LiveCashFutureCommonRunner"]