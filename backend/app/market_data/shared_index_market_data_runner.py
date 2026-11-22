"""Isolated live-data runner for the locked NIFTY/BANKNIFTY market universe.

Starts spot + option + futures subscriptions on the existing shared Angel One feed.
It publishes latest tick state for future strategy consumers. Application lifespan starts the live feed; no strategy or order logic is connected.
"""
from __future__ import annotations

from datetime import date, datetime
from threading import RLock, Thread
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from .instruments import InstrumentMaster
from .shared_index_feed_lifecycle import SharedIndexFeedLifecycle
from .shared_index_market_data_state import SharedIndexMarketDataState
from .shared_index_option_rotation_controller import SharedIndexOptionRotationController
from .shared_index_option_selection import select_locked_index_options
from .shared_index_spot_resolver import resolve_locked_index_spot_descriptors
from .shared_index_option_selection import _parse_expiry
from .box_spread_expiry import classify_index_expiry_dates
from .shared_index_derived_iv import SharedIndexDerivedIV


IST = ZoneInfo("Asia/Kolkata")
LOCKED = ("NIFTY", "BANKNIFTY")


def _expiry_candidates(rows: Iterable[dict], underlying: str) -> list[date]:
    dates = set()
    for row in rows:
        if (str(row.get("exch_seg", "")).strip().upper() != "NFO"
                or str(row.get("name", "")).strip().upper() != underlying
                or str(row.get("instrumenttype", "")).strip().upper() != "OPTIDX"):
            continue
        try:
            dates.add(_parse_expiry(row.get("expiry")))
        except (ValueError, TypeError):
            continue
    return sorted(dates)


def choose_locked_expiry(rows: Iterable[dict], underlying: str, *, today: date | None = None) -> str:
    """Choose next NIFTY weekly or BANKNIFTY monthly expiry using exchange classification."""
    symbol = str(underlying).strip().upper()
    if symbol not in LOCKED:
        raise ValueError("expiry selection is restricted to NIFTY/BANKNIFTY")
    current = today or datetime.now(IST).date()
    all_dates = _expiry_candidates(rows, symbol)
    future_dates = [expiry for expiry in all_dates if expiry >= current]
    if not future_dates:
        raise LookupError(f"no future option expiries listed for {symbol}")
    # Classify against all dates. Removing expired dates first could wrongly
    # promote a remaining weekly expiry to monthly for NIFTY.
    classification = classify_index_expiry_dates(symbol, all_dates)
    if symbol == "NIFTY":
        weekly = sorted(expiry for expiry in future_dates if classification.get(expiry) == "WEEKLY")
        if not weekly:
            raise LookupError("no future NIFTY weekly expiry in listed master")
        return weekly[0].isoformat()
    monthly = sorted(expiry for expiry in future_dates if classification.get(expiry) == "MONTHLY")
    if not monthly:
        raise LookupError("no future BANKNIFTY monthly expiry in listed master")
    return monthly[0].isoformat()


class SharedIndexMarketDataRunner:
    """Explicit ON/OFF lifecycle; isolated from application startup and strategy logic."""

    def __init__(self, *, instrument_master: Any | None = None,
                 manager: Any | None = None,
                 publish: Callable[[Any], None] | None = None,
                 rotation_thresholds: dict[str, float] | None = None,
                 cooldown_seconds: float = 1.0,
                 clock=None):
        self.instrument_master = instrument_master or InstrumentMaster()
        self.state = SharedIndexMarketDataState(publish=publish)
        self.derived_iv = SharedIndexDerivedIV()
        self.lifecycle = SharedIndexFeedLifecycle(manager=manager, record_callback=self._on_record)
        thresholds = rotation_thresholds or {"NIFTY": 20.0, "BANKNIFTY": 50.0}
        controller_kwargs = {
            "lifecycle": self.lifecycle,
            "instrument_rows": lambda: tuple(self._rows),
            "expiry_for": self._expiry_for,
            "thresholds": thresholds,
            "cooldown_seconds": cooldown_seconds,
        }
        if clock is not None:
            controller_kwargs["clock"] = clock
        self.rotation = SharedIndexOptionRotationController(**controller_kwargs)
        self._lock = RLock()
        self._rows: tuple[dict, ...] = ()
        self._running = False
        self._start_error: str | None = None
        self._started_at: str | None = None
        self._bootstrap_options_done = False
        self._bootstrap_inflight = False
        self._spot_records: dict[str, Any] = {}
        self._futures_expiries: dict[str, str] = {}
        self._master_refresh_interval_seconds = 15 * 60.0
        self._master_refresh_started_at = 0.0
        self._master_refresh_inflight = False
        self._master_last_refresh_at: str | None = None
        self._master_refresh_error: str | None = None
        self._run_generation = 0
        self._master_refresh_generation: int | None = None

    @staticmethod
    def choose_nearest_future(rows: Iterable[dict], underlying: str, *, today: date | None = None) -> dict:
        symbol = str(underlying).strip().upper()
        if symbol not in LOCKED:
            raise ValueError("futures selection is restricted to NIFTY/BANKNIFTY")
        current = today or datetime.now(IST).date()
        candidates = []
        for row in rows:
            if (str(row.get("exch_seg", "")).strip().upper() != "NFO"
                    or str(row.get("name", "")).strip().upper() != symbol
                    or str(row.get("instrumenttype", "")).strip().upper() != "FUTIDX"):
                continue
            try:
                expiry = _parse_expiry(row.get("expiry"))
            except (TypeError, ValueError):
                continue
            if expiry < current:
                continue
            token = str(row.get("token", "")).strip()
            trading_symbol = str(row.get("symbol", "")).strip()
            if token and trading_symbol:
                candidates.append((expiry, token, trading_symbol, row))
        if not candidates:
            raise LookupError(f"no active NFO FUTIDX contract listed for {symbol}")
        expiry, token, trading_symbol, row = min(candidates, key=lambda item: item[0])
        return {"underlying": symbol, "expiry": expiry.isoformat(), "token": token,
                "symbol": trading_symbol, "lot_size": row.get("lotsize")}

    def _expiry_for(self, underlying: str) -> str:
        return choose_locked_expiry(self._rows, underlying)

    def _schedule_master_refresh(self) -> bool:
        """Refresh instrument master off tick callback and publish complete snapshots only."""
        import time
        with self._lock:
            if not self._running or self._master_refresh_inflight:
                return False
            if time.monotonic() - self._master_refresh_started_at < self._master_refresh_interval_seconds:
                return False
            self._master_refresh_inflight = True
            generation = self._run_generation
            self._master_refresh_generation = generation
            self._master_refresh_started_at = time.monotonic()

        def worker():
            try:
                refreshed = tuple(self.instrument_master.download(force=True))
                if not refreshed:
                    raise LookupError("refreshed instrument master was empty")
                with self._lock:
                    # Guard publication and contract rotations against OFF/restart.
                    if not self._running or generation != self._run_generation:
                        return
                    self._rows = refreshed
                    self._master_last_refresh_at = datetime.now(IST).isoformat()
                    self._master_refresh_error = None
                    spots = dict(self._spot_records)
                    options_ready = self._bootstrap_options_done
                    if options_ready:
                        for underlying in LOCKED:
                            record = spots.get(underlying)
                            if record is not None and record.ltp is not None:
                                self.rotation._rotate(underlying, float(record.ltp))
                        self._check_futures_rollover()
                        self._sync_state_universe()
            except Exception as exc:
                with self._lock:
                    self._master_refresh_error = f"{type(exc).__name__}: {exc}"[:200]
            finally:
                with self._lock:
                    if self._master_refresh_generation == generation:
                        self._master_refresh_inflight = False
                        self._master_refresh_generation = None

        Thread(target=worker, name="shared-index-master-refresh", daemon=True).start()
        return True

    def _check_futures_rollover(self) -> tuple[str, ...]:
        """Roll expired index futures without dropping the current subscription first."""
        try:
            from .common_strategy_feed import CommonStrategyMarketFeed
            replacements = []
            expiry_updates = {}
            for underlying in LOCKED:
                selected = self.choose_nearest_future(self._rows, underlying)
                if self._futures_expiries.get(underlying) == selected["expiry"]:
                    continue
                raw_lot = selected["lot_size"]
                lot_size = int(float(raw_lot)) if raw_lot else None
                replacements.append(CommonStrategyMarketFeed.descriptor(
                    exchange="NFO", token=selected["token"], symbol=selected["symbol"],
                    instrument_type="future", segment="NFO", expiry=selected["expiry"],
                    lot_size=lot_size, underlying=underlying,
                ))
                expiry_updates[underlying] = selected["expiry"]
            if not replacements:
                return ()
            # Keep unchanged underlying's existing future descriptor alongside replacements.
            futures_manager = self.lifecycle._futures_feed.manager
            current_futures = [
                descriptor for key in self.lifecycle._futures_keys
                if (descriptor := futures_manager.registry.get(key)) is not None
                and descriptor.underlying not in expiry_updates
            ]
            self.lifecycle.rotate_futures(tuple(current_futures + replacements))
            self._futures_expiries.update(expiry_updates)
            self._sync_state_universe()
            return tuple(expiry_updates)
        except Exception as exc:
            with self._lock:
                self._start_error = f"Futures rollover: {type(exc).__name__}: {exc}"[:240]
            return ()

    def _bootstrap_options(self) -> bool:
        # Bootstrap only after both underlyings have independent fresh spot ticks.
        # This avoids using NIFTY spot as the BANKNIFTY ATM reference.
        with self._lock:
            if self._bootstrap_options_done or self._bootstrap_inflight:
                return False
            if set(self._spot_records) != set(LOCKED):
                return False
            now_ns = __import__("time").time_ns()
            if any(now_ns - record.timestamp_ns > self.state.max_age_ns
                   or record.timestamp_ns - now_ns > 1_000_000_000
                   for record in self._spot_records.values()):
                return False
            self._bootstrap_inflight = True
            rows = self._rows
            spot_prices = {underlying: float(record.ltp) for underlying, record in self._spot_records.items()}
        try:
            from .common_strategy_feed import CommonStrategyMarketFeed
            descriptors = []
            for underlying in LOCKED:
                expiry = choose_locked_expiry(rows, underlying)
                selected = select_locked_index_options(
                    rows, underlying=underlying, expiry=expiry,
                    spot_price=spot_prices[underlying],
                )
                descriptors.extend(CommonStrategyMarketFeed.descriptor(
                    exchange="NFO", token=contract.token, symbol=contract.trading_symbol,
                    instrument_type="option", segment="NFO", expiry=contract.expiry,
                    strike=contract.strike, option_type=contract.option_type,
                    lot_size=contract.lot_size, underlying=contract.underlying,
                ) for contract in selected.contracts)
            with self._lock:
                if not self._running or not self._bootstrap_inflight:
                    return False
                self.lifecycle.start_options(tuple(descriptors))
                self._sync_state_universe()
                self._start_error = None
                self._bootstrap_options_done = True
                self._bootstrap_inflight = False
            return True
        except Exception as exc:
            with self._lock:
                self._start_error = f"{type(exc).__name__}: {exc}"[:240]
                self._bootstrap_inflight = False
            return False

    def _sync_state_universe(self) -> None:
        # Bound tick-state keys to the currently owned contracts after rotations.
        # Cache entries for retired strikes must not consume the 50-key cap.
        self.state.retain_instruments(self.lifecycle.active_keys())

    def _on_record(self, record) -> None:
        with self._lock:
            if not self._running:
                return
            accepted = self.state.on_record(record)
        if not accepted:
            return
        if getattr(record, "instrument_type", None) is not None and getattr(record.instrument_type, "value", "") == "index":
            underlying = str(getattr(record, "underlying", "") or "").strip().upper()
            if underlying not in LOCKED or record.ltp is None:
                return
            with self._lock:
                running = self._running
                if running:
                    self._spot_records[underlying] = record
            if not running:
                return
            self._bootstrap_options()
            with self._lock:
                # Stop and rotate are serialized; no subscription can be added
                # after an explicit OFF lifecycle completes.
                if not self._running or not self._bootstrap_options_done:
                    return
                rotated = self.rotation.on_record(record)
                if rotated:
                    self._sync_state_universe()
                flushed = self.rotation.flush_due()
                if flushed:
                    self._sync_state_universe()
                self._check_futures_rollover()
                self._schedule_master_refresh()

    def start(self, instrument_rows: Iterable[dict] | None = None) -> dict:
        with self._lock:
            if self._running:
                return self.snapshot()
            self._run_generation += 1
            self._master_refresh_inflight = False
            self._master_refresh_generation = None
            self._start_error = None
            self._spot_records.clear()
            self._futures_expiries.clear()
            self.state.clear()
            self.derived_iv.clear()
            self._bootstrap_options_done = False
            self._bootstrap_inflight = False
        try:
            rows = tuple(instrument_rows) if instrument_rows is not None else tuple(self.instrument_master.download())
            if not rows:
                raise LookupError("Angel One instrument master is empty")
            spots = resolve_locked_index_spot_descriptors(self.instrument_master)
            with self._lock:
                self._rows = rows
            with self._lock:
                self.lifecycle.start_spot(spots)
                self._running = True
                self._started_at = datetime.now(IST).isoformat()
                self._master_refresh_started_at = __import__("time").monotonic()
            from .common_strategy_feed import CommonStrategyMarketFeed
            futures = []
            future_expiries = {}
            for underlying in LOCKED:
                selected = self.choose_nearest_future(rows, underlying)
                future_expiries[underlying] = selected["expiry"]
                raw_lot = selected["lot_size"]
                lot_size = int(float(raw_lot)) if raw_lot else None
                futures.append(CommonStrategyMarketFeed.descriptor(
                    exchange="NFO", token=selected["token"], symbol=selected["symbol"],
                    instrument_type="future", segment="NFO", expiry=selected["expiry"],
                    lot_size=lot_size, underlying=underlying,
                ))
            self.lifecycle.start_futures(tuple(futures))
            self._futures_expiries = future_expiries
            return self.snapshot()
        except Exception as exc:
            with self._lock:
                self._running = False
                self._bootstrap_options_done = False
                self._bootstrap_inflight = False
                self._spot_records.clear()
                self._futures_expiries.clear()
                self._started_at = None
                self.state.clear()
                self.derived_iv.clear()
                self._start_error = f"{type(exc).__name__}: {exc}"[:240]
            try:
                self.lifecycle.stop()
            except Exception:
                pass
            return self.snapshot()

    def stop(self) -> dict:
        with self._lock:
            self._run_generation += 1
            self._running = False
            self._bootstrap_options_done = False
            self._bootstrap_inflight = False
            self._spot_records.clear()
            self.state.clear()
            self.derived_iv.clear()
        try:
            self.lifecycle.stop()
        finally:
            with self._lock:
                self._started_at = None
        return self.snapshot()

    def flush_rotations(self) -> tuple[str, ...]:
        # Serialize external pending flushes with live spot callbacks. Successful
        # rotations must immediately retire old keys from state/cache so late
        # frames cannot resurrect stale quotes between callback-driven syncs.
        with self._lock:
            if not self._running:
                return ()
            completed = self.rotation.flush_due()
            if completed:
                self._sync_state_universe()
            return completed

    def latest(self, instrument_key: str):
        return self.state.latest(instrument_key)

    def snapshot(self) -> dict:
        with self._lock:
            running = self._running
            started_at = self._started_at
            error = self._start_error
            bootstrap_done = self._bootstrap_options_done
        state = self.state.snapshot()
        lifecycle = self.lifecycle.snapshot()
        rotation = self.rotation.snapshot()
        now_ns = __import__("time").time_ns()
        with self._lock:
            spot_records = dict(self._spot_records)
        latest_spots = {
            symbol: {
                "price": record.ltp,
                "timestamp_ns": record.timestamp_ns,
                "age_seconds": max(0.0, (now_ns - record.timestamp_ns) / 1e9),
            }
            for symbol, record in spot_records.items()
        }
        spot_prices = {symbol: record.ltp for symbol, record in spot_records.items() if record.ltp is not None and now_ns - record.timestamp_ns <= self.state.max_age_ns}
        enriched_records = self.derived_iv.decorate(self.state.records_snapshot(), spot_prices)
        aggregates = {}
        for underlying in LOCKED:
            side = {"CE": {}, "PE": {}}
            aggregate_fields = ("oi", "volume", "oi_delta_1s", "oi_delta_3s", "oi_delta_5s", "volume_delta_1s", "volume_delta_3s", "volume_delta_5s")
            valid_counts = {"CE": {}, "PE": {}}
            for record in enriched_records:
                if record.get("underlying") != underlying or record.get("instrument_type") != "option" or not record.get("fresh"):
                    continue
                option_side = record.get("option_type")
                if option_side not in side:
                    continue
                bucket = side[option_side]
                for field in aggregate_fields:
                    value = record.get(field)
                    if value is None:
                        continue
                    bucket[field] = bucket.get(field, 0) + value
                    valid_counts[option_side][field] = valid_counts[option_side].get(field, 0) + 1
            # Missing short-window deltas stay None instead of being disguised as zero.
            for option_side, bucket in side.items():
                for field in aggregate_fields:
                    if valid_counts[option_side].get(field, 0) == 0:
                        bucket[field] = None
            calls, puts = side["CE"], side["PE"]
            call_oi, put_oi = calls.get("oi", 0), puts.get("oi", 0)
            call_volume, put_volume = calls.get("volume", 0), puts.get("volume", 0)
            aggregates[underlying] = {
                "call": calls, "put": puts,
                "oi_put_call_ratio": (put_oi / call_oi) if call_oi is not None and put_oi is not None and call_oi > 0 else None,
                "oi_side_imbalance": ((put_oi - call_oi) / (put_oi + call_oi)) if put_oi is not None and call_oi is not None and put_oi + call_oi > 0 else None,
                "volume_side_imbalance": ((put_volume - call_volume) / (put_volume + call_volume)) if put_volume is not None and call_volume is not None and put_volume + call_volume > 0 else None,
            }
        for row in enriched_records:
            underlying = str(row.get("underlying") or "").upper()
            spot = spot_prices.get(underlying)
            strike = row.get("strike")
            if row.get("instrument_type") == "option" and spot is not None and strike is not None:
                row["distance_from_spot"] = float(strike) - float(spot)
                row["distance_from_spot_pct"] = (float(strike) - float(spot)) / float(spot) * 100.0 if float(spot) > 0 else None
                row["moneyness"] = "ATM" if abs(float(strike) - float(spot)) <= max(float(row.get("tick_size") or 0), 1.0) else ("ITM" if (row.get("option_type") == "CE" and float(strike) < float(spot)) or (row.get("option_type") == "PE" and float(strike) > float(spot)) else "OTM")
            else:
                row["distance_from_spot"] = None
                row["distance_from_spot_pct"] = None
                row["moneyness"] = None
        return {
            "runner": "shared-index-market-data",
            "enabled": running,
            "started_at": started_at,
            "last_error": error,
            "initial_option_bootstrap_done": bootstrap_done,
            "spot_universe": list(LOCKED),
            "latest_spots": latest_spots,
            "futures_expiries": dict(self._futures_expiries),
            "future_selection": "nearest listed non-expired NFO FUTIDX per underlying",
            "option_universe": {"NIFTY": "weekly ATM +/- 6", "BANKNIFTY": "monthly ATM +/- 5"},
            "state": state,
            "records": enriched_records,
            "option_flow_aggregates": aggregates,
            "derived_iv_model": {"method": "Black-Scholes", "source": "derived_bsm", "risk_free_rate": 0.065, "risk_free_rate_unit": "annual_decimal", "expiry_time_ist": "15:30", "iv_unit": "percent", "velocity_windows_seconds": [1, 3, 5], "quote_metrics": ["mid_price", "microprice", "spread_pct", "quote_imbalance", "tick_rate_1s"], "option_flow_aggregate_windows_seconds": [1, 3, 5], "volume_source": "broker_day_volume", "volume_delta_window_seconds": 1, "oi_source": "broker_open_interest", "oi_delta_window_seconds": 1},
            "lifecycle": lifecycle,
            "rotation": rotation,
            "live_data_ready": running and bootstrap_done and state["instrument_count"] == 52 and state["fresh_count"] == 52,
        }


__all__ = ["SharedIndexMarketDataRunner", "choose_locked_expiry"]
