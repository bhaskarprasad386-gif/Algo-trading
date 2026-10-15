"""Live Cash-Future runner backed by the common WebSocket/normalization layer."""
from __future__ import annotations

import threading
import time
from math import isfinite
from dataclasses import replace
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Any, Callable

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
        self._quote_diagnostics_lock = threading.Lock()
        self._quote_diagnostics: dict[str, int] = {
            "records": 0, "cash_records": 0, "future_records": 0,
            "missing_ltp": 0, "missing_bid": 0, "missing_ask": 0,
            "missing_both_sides": 0, "buy_depth_present": 0,
            "sell_depth_present": 0, "buy_depth_present_bid_missing": 0,
            "sell_depth_present_ask_missing": 0,
            "buy_depth_levels_examined": 0, "sell_depth_levels_examined": 0,
            "buy_depth_usable_price": 0, "sell_depth_usable_price": 0,
            "buy_depth_present_without_usable_price": 0,
            "sell_depth_present_without_usable_price": 0,
            "buy_direct_price_present": 0, "sell_direct_price_present": 0,
            "normalized_crossed_book": 0, "normalized_missing_bid": 0,
            "normalized_missing_ask": 0, "normalized_missing_both_sides": 0,
        }
        # Provenance counters only; never store the raw broker timestamp value.
        self._timestamp_diagnostics: dict[str, int] = {
            "broker_ns": 0, "broker_ms": 0, "fallback_missing": 0,
            "fallback_invalid": 0, "invalid": 0, "unknown": 0,
        }
        # Stage-by-stage universe accounting; symbol samples are bounded and
        # contain identifiers/reasons only, never raw broker payloads.
        self._universe_diagnostics: dict[str, Any] = {
            "instrument_master_rows": 0,
            "configured_symbols": 0,
            "futstk_rows": 0,
            "eligible_future_contracts": 0,
            "unique_future_contracts": 0,
            "cash_resolved_contracts": 0,
            "cash_resolution_failures": 0,
            "selected_current_near_contracts": 0,
            "descriptors_built": 0,
            "descriptors_accepted": 0,
            "descriptor_conflicts": 0,
            "subscription_keys_requested": 0,
            "subscription_keys_confirmed": 0,
            "cash_resolution_failure_samples": [],
            "descriptor_conflict_samples": [],
            "last_subscription_at_ns": None,
        }

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
            "quote_diagnostics": self._quote_diagnostics_snapshot(),
            "timestamp_diagnostics": dict(self._timestamp_diagnostics),
            "universe_diagnostics": dict(self._universe_diagnostics),
        }

    @staticmethod
    def _has_depth(payload: Any, *keys: str) -> bool:
        if not isinstance(payload, dict):
            return False
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict) and bool(value):
                return True
            if isinstance(value, list) and bool(value):
                return True
        return False

    @staticmethod
    def _depth_price_diagnostic(payload: Any, side: str) -> tuple[int, bool, bool]:
        """Return examined level count, usable depth price, and direct-price presence."""
        if not isinstance(payload, dict):
            return 0, False, False
        direct_names = (f"{side}_price", "bid" if side == "buy" else "ask")
        direct_present = False
        for name in direct_names:
            try:
                value = float(payload.get(name))
            except (TypeError, ValueError, OverflowError):
                continue
            if isfinite(value) and value > 0:
                direct_present = True
                break
        keys = (f"best_5_{side}_data", f"best_{side}_data", f"best_{side}", side)
        examined = 0
        for key in keys:
            value = payload.get(key)
            levels = [value] if isinstance(value, dict) else value if isinstance(value, list) else ()
            for level in levels:
                examined += 1
                if not isinstance(level, dict):
                    continue
                for name in ("price", f"{side}_price"):
                    try:
                        price = float(level.get(name))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if isfinite(price) and price > 0:
                        return examined, True, direct_present
        return examined, False, direct_present

    def _quote_diagnostics_snapshot(self) -> dict[str, int]:
        with self._quote_diagnostics_lock:
            return dict(self._quote_diagnostics)

    def _record_quote_diagnostics(self, record: MarketDataRecord, meta: dict[str, Any]) -> None:
        """Compare raw depth presence with normalized quote sides; retain counters only."""
        raw = record.payload
        buy_depth = self._has_depth(raw, "best_5_buy_data", "best_buy_data", "best_buy", "buy")
        sell_depth = self._has_depth(raw, "best_5_sell_data", "best_sell_data", "best_sell", "sell")
        missing_ltp = record.ltp is None or record.ltp <= 0
        missing_bid = record.bid is None or record.bid <= 0
        missing_ask = record.ask is None or record.ask <= 0
        buy_examined, buy_usable, buy_direct = self._depth_price_diagnostic(raw, "buy")
        sell_examined, sell_usable, sell_direct = self._depth_price_diagnostic(raw, "sell")
        reason = raw.get("_quote_normalization_reason")
        with self._quote_diagnostics_lock:
            stats = self._quote_diagnostics
            stats["records"] += 1
            stats["cash_records" if meta["leg"] == "CASH" else "future_records"] += 1
            stats["missing_ltp"] += int(missing_ltp)
            stats["missing_bid"] += int(missing_bid)
            stats["missing_ask"] += int(missing_ask)
            stats["missing_both_sides"] += int(missing_bid and missing_ask)
            stats["buy_depth_present"] += int(buy_depth)
            stats["sell_depth_present"] += int(sell_depth)
            stats["buy_depth_present_bid_missing"] += int(buy_depth and missing_bid)
            stats["sell_depth_present_ask_missing"] += int(sell_depth and missing_ask)
            stats["buy_depth_levels_examined"] += buy_examined
            stats["sell_depth_levels_examined"] += sell_examined
            stats["buy_depth_usable_price"] += int(buy_usable)
            stats["sell_depth_usable_price"] += int(sell_usable)
            stats["buy_depth_present_without_usable_price"] += int(buy_depth and not buy_usable)
            stats["sell_depth_present_without_usable_price"] += int(sell_depth and not sell_usable)
            stats["buy_direct_price_present"] += int(buy_direct)
            stats["sell_direct_price_present"] += int(sell_direct)
            if reason in {"crossed_book", "missing_bid", "missing_ask", "missing_both_sides"}:
                stats[f"normalized_{reason}"] += 1

    def _build_descriptors(self) -> tuple[InstrumentDescriptor, ...]:
        rows = self.instrument_master.download()
        configured = {x.strip().upper() for x in settings.LIVE_CASH_FUTURE_SYMBOLS.split(",") if x.strip()}
        universe = configured or None
        self._universe_diagnostics = {
            "instrument_master_rows": len(rows),
            "configured_symbols": len(configured),
            "futstk_rows": 0,
            "eligible_future_contracts": 0,
            "unique_future_contracts": 0,
            "cash_resolved_contracts": 0,
            "cash_resolution_failures": 0,
            "selected_current_near_contracts": 0,
            "descriptors_built": 0,
            "descriptors_accepted": 0,
            "descriptor_conflicts": 0,
            "subscription_keys_requested": 0,
            "subscription_keys_confirmed": 0,
            "cash_resolution_failure_samples": [],
            "descriptor_conflict_samples": [],
            "last_subscription_at_ns": None,
        }
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        futures: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if str(row.get("exch_seg", "")).upper() != "NFO":
                continue
            if str(row.get("instrumenttype", "")).upper() != "FUTSTK":
                continue
            self._universe_diagnostics["futstk_rows"] += 1
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
        self._universe_diagnostics["eligible_future_contracts"] = len(futures)
        self._universe_diagnostics["unique_future_contracts"] = len(seen)
        futures.sort(key=lambda x: (x["_underlying"], x["_expiry"], str(x.get("symbol"))))
        selected: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for row in futures:
            underlying = row["_underlying"]
            if counts.get(underlying, 0) >= 2:
                continue
            try:
                cash = self.instrument_master.resolve_cash_instrument(underlying, "NSE")
            except (LookupError, ValueError) as exc:
                self._universe_diagnostics["cash_resolution_failures"] += 1
                samples = self._universe_diagnostics["cash_resolution_failure_samples"]
                if len(samples) < 20:
                    samples.append({"underlying": underlying, "reason": type(exc).__name__})
                continue
            self._universe_diagnostics["cash_resolved_contracts"] += 1
            item = dict(row)
            item["_cash"] = cash
            item["_month"] = "CURRENT" if counts.get(underlying, 0) == 0 else "NEAR"
            selected.append(item)
            counts[underlying] = counts.get(underlying, 0) + 1
        self._universe_diagnostics["selected_current_near_contracts"] = len(selected)

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
        self._universe_diagnostics["descriptors_built"] = len(descriptors)
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

    def _record_payload(self, record: MarketDataRecord, meta: dict[str, Any]) -> dict[str, Any]:
        payload = record.as_dict()
        provenance = record.payload.get("_timestamp_provenance")
        payload.update({
            "source_timestamp_ns": record.timestamp_ns,
            "timestamp_provenance": provenance if isinstance(provenance, str) and provenance in self._timestamp_diagnostics else "unknown",
            "leg": meta["leg"],
            "underlying": meta["underlying"],
            "contract_month": meta["contract_month"],
            "received_at_ns": (
                record.payload.get("_received_at_ns")
                if isinstance(record.payload.get("_received_at_ns"), int)
                and record.payload.get("_received_at_ns") > 0
                else time.time_ns()
            ),
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
        self._records_received += 1
        provenance = record.payload.get("_timestamp_provenance")
        provenance_key = provenance if isinstance(provenance, str) and provenance in self._timestamp_diagnostics else "unknown"
        self._timestamp_diagnostics[provenance_key] += 1
        meta = self._metadata.get(record.instrument)
        if meta is None:
            self._records_missing_metadata += 1
            return
        # The broker tick usually contains no underlying field. The runner's
        # instrument-master mapping is authoritative and must be attached before
        # the scanner pairs cash symbols with their current/near futures.
        if record.underlying != meta["underlying"]:
            record = replace(record, underlying=meta["underlying"])
        self._record_quote_diagnostics(record, meta)
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
            # The payload callback is the authoritative scanner path when supplied.
            # Do not also feed the same record into the legacy opportunity scanner.
            try:
                self.on_payload(self._record_payload(record, meta))
                self._payload_callbacks += 1
            except Exception as exc:
                app_logger.error("Cash-Future payload callback failed: %s", exc)
            return
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
                self._universe_diagnostics["descriptor_conflicts"] += 1
                samples = self._universe_diagnostics["descriptor_conflict_samples"]
                if len(samples) < 20:
                    samples.append({"instrument": descriptor.key.value, "reason": "incompatible_shared_descriptor"})
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
        self._universe_diagnostics["descriptors_accepted"] = len(accepted)
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
                        subscriptions = self.manager.subscribe(self.CONSUMER, keys, mode=3)
                        subscribed = True
                        self._universe_diagnostics["subscription_keys_requested"] = len(keys)
                        self._universe_diagnostics["subscription_keys_confirmed"] = len(subscriptions)
                        self._universe_diagnostics["last_subscription_at_ns"] = time.time_ns()
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