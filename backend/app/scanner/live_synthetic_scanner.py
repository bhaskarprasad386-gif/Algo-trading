"""Low-latency live synthetic Cash-Carry scanner.

Consumes the normalized one-second observations emitted by the live synthetic
recorder. It assembles real CE+PE+future quotes at the same timestamp and then
delegates pricing/ranking to the existing pure synthetic scanner.
"""

from __future__ import annotations

from collections import Counter
import math
from datetime import datetime, time as datetime_time
from dataclasses import replace
from zoneinfo import ZoneInfo
from threading import Lock
import time
from typing import Callable

from app.backtesting.arbitrage_backtester import FutureQuote, OptionQuote
from app.scanner.synthetic_cash_carry import SyntheticScanConfig, SyntheticScanResult, scan_synthetic_snapshot


class LiveSyntheticScanner:
    """Bounded in-memory assembler for one-second synthetic opportunities."""

    MAX_ACTIVE_BUCKETS = 4096
    MAX_BUCKET_AGE_NS = 3_000_000_000

    def __init__(
        self,
        *,
        atm_provider: Callable[[str, int], float | None],
        config_provider: Callable[[str], SyntheticScanConfig] | None = None,
        on_result: Callable[[tuple[SyntheticScanResult, ...]], None] | None = None,
    ) -> None:
        self.atm_provider = atm_provider
        self.config_provider = config_provider or (lambda _symbol: SyntheticScanConfig())
        self.on_result = on_result
        self._lock = Lock()
        self._diagnostics_lock = Lock()
        self._diagnostics: Counter[str] = Counter()
        # Separate transport activity from accepted market observations: a
        # stream of stale/invalid ticks must not make scanner health look fresh.
        self._last_payload_received_at_ns: int | None = None
        self._last_observation_at_ns: int | None = None
        self._last_successful_calculation_at_ns: int | None = None
        self._last_result_count = 0
        self._buckets: dict[tuple[str, int, int], dict] = {}

    def _count(self, reason: str, amount: int = 1) -> None:
        with self._diagnostics_lock:
            self._diagnostics[reason] += amount

    def _record_source_latency(self, latency_ns: int) -> None:
        """Record bounded source-to-scanner latency buckets; never retain quote data."""
        self._count("source_latency_samples")
        if latency_ns < 0:
            self._count("source_latency_negative")
        elif latency_ns <= 250_000_000:
            self._count("source_latency_0_250ms")
        elif latency_ns <= 1_000_000_000:
            self._count("source_latency_250ms_1s")
        elif latency_ns <= 5_000_000_000:
            self._count("source_latency_1_5s")
        else:
            self._count("source_latency_over_5s")

    def diagnostics_snapshot(self) -> dict:
        """Return scanner-local counters without exposing cached market payloads."""
        # Keep lock order consistent with observe(): scanner state before counters.
        with self._lock:
            self._prune(max((key[1] for key in self._buckets), default=0))
            with self._diagnostics_lock:
                counters = dict(self._diagnostics)
            counters["active_buckets"] = len(self._buckets)
            counters["last_payload_received_at_ns"] = self._last_payload_received_at_ns
            counters["last_observation_at_ns"] = self._last_observation_at_ns
            counters["last_successful_calculation_at_ns"] = self._last_successful_calculation_at_ns
            counters["last_result_count"] = self._last_result_count
        return {"counters": counters}

    @staticmethod
    def _expiry(value: object) -> int:
        text = str(value or "").strip().upper()
        for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return int(datetime.strptime(text, fmt).strftime("%Y%m%d"))
            except ValueError:
                continue
        raise ValueError(f"invalid option/future expiry: {value!r}")

    @staticmethod
    def _price(value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    def observe(self, payload: dict) -> tuple[SyntheticScanResult, ...]:
        self._count("observations_received")
        received_at_ns = time.time_ns()
        with self._lock:
            self._last_payload_received_at_ns = received_at_ns
        if not isinstance(payload, dict):
            self._count("invalid_payload_or_scope")
            return ()
        symbol = str(payload.get("underlying") or "").strip().upper()
        cls = str(payload.get("instrument_class") or "").strip().upper()
        option_type = str(payload.get("option_type") or "").strip().upper()
        raw_timestamp = payload.get("source_timestamp_ns")
        # JSON numeric timestamps must be integral. int(1.9) silently truncates
        # to 1 and bool is an int subclass; neither is a valid source timestamp.
        if isinstance(raw_timestamp, bool) or (
            isinstance(raw_timestamp, float)
            and (not math.isfinite(raw_timestamp) or not raw_timestamp.is_integer())
        ):
            self._count("invalid_timestamp")
            return ()
        try:
            timestamp_ns = int(raw_timestamp or 0)
        except (TypeError, ValueError, OverflowError):
            self._count("invalid_timestamp")
            return ()
        if timestamp_ns <= 0:
            self._count("invalid_timestamp")
            return ()
        # Epoch milliseconds (roughly 13 digits) are not valid nanoseconds. Treat
        # them as malformed rather than letting them bypass the epoch-ns freshness
        # guard as if they were deterministic test timestamps.
        if 1_000_000_000_000 <= timestamp_ns < 1_000_000_000_000_000:
            self._count("invalid_timestamp_unit")
            return ()
        # Epoch-nanosecond timestamps are checked against wall time. Small logical
        # timestamps remain supported by deterministic unit tests only.
        if timestamp_ns >= 1_000_000_000_000_000:
            # Measure latency before freshness rejection so stale samples remain
            # observable. Counters are aggregate-only; no raw payload is kept.
            now_ns = time.time_ns()
            self._record_source_latency(now_ns - timestamp_ns)
            if timestamp_ns > now_ns + 5_000_000_000:
                self._count("future_timestamp")
                return ()
            if now_ns - timestamp_ns > 5_000_000_000:
                self._count("stale_timestamp")
                return ()
        if not symbol or cls not in {"STOCK", "INDEX"}:
            self._count("invalid_payload_or_scope")
            return ()
        bid = self._price(payload.get("bid"))
        ask = self._price(payload.get("ask"))
        ltp = self._price(payload.get("ltp") or payload.get("last_traded_price"))
        # LTP establishes that a leg exists; executable bid/ask may follow on a
        # subsequent broker tick. Keep that leg pending for the bounded quote wait.
        quote_missing = bid is None or ask is None
        if (quote_missing and ltp is None) or (not quote_missing and ask < bid):
            self._count("invalid_quote")
            return ()

        def positive_number(value: object) -> float:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return 0.0
            return max(number, 0.0) if math.isfinite(number) else 0.0

        def nonnegative_int(value: object) -> int:
            try:
                number = float(value or 0)
            except (TypeError, ValueError, OverflowError):
                return 0
            if not math.isfinite(number) or number <= 0:
                return 0
            return int(number)

        def liquid_leg(option: dict) -> bool:
            # Executability requires a valid two-sided quote with positive depth.
            # Volume/OI may be absent on individual WebSocket ticks and are metadata,
            # not a reason to discard an otherwise executable opportunity.
            return (
                self._price(option.get("bid")) is not None
                and self._price(option.get("ask")) is not None
                and self._price(option.get("ask")) >= self._price(option.get("bid"))
                and positive_number(option.get("bid_qty")) > 0
                and positive_number(option.get("ask_qty")) > 0
            )

        try:
            expiry = self._expiry(payload.get("expiry"))
        except ValueError:
            self._count("invalid_expiry")
            return ()
        strike = self._price(payload.get("strike"))
        if option_type in {"CE", "PE"} and strike is None:
            self._count("invalid_strike")
            return ()
        # An empty option_type is the legacy future-leg representation. Do not
        # let a cash/underlying quote with no option_type occupy the future slot:
        # it shares the same underlying/expiry bucket and could create a false
        # synthetic triplet. Every non-option leg must identify itself as a future.
        instrument_symbol = str(payload.get("symbol") or "").strip().upper()
        # Subscription metadata is keyed by instrument token, but validate the
        # normalized symbol against the declared underlying too: a valid FUT/CE/PE
        # suffix alone must not let another underlying enter this bucket.
        if not instrument_symbol.startswith(symbol):
            self._count("underlying_symbol_mismatch")
            return ()
        if option_type not in {"", "CE", "PE"}:
            self._count("invalid_option_type")
            return ()
        if option_type in {"CE", "PE"} and not instrument_symbol.endswith(option_type):
            self._count("invalid_option_instrument")
            return ()
        if option_type not in {"CE", "PE"} and not instrument_symbol.endswith("FUT"):
            self._count("invalid_future_instrument")
            return ()
        with self._lock:
            key = self._matching_bucket_key(symbol, timestamp_ns, expiry)
            bucket = self._buckets.setdefault(
                key,
                {"future": None, "options": {}, "first_seen_monotonic_ns": time.monotonic_ns()},
            )
            # A later executable-book tick may complete an LTP-selected leg. Keep
            # its original pairing timestamp only when it is the same instrument
            # and arrives inside the explicit three-second quote-wait window.
            def preserve_pending_anchor(previous: dict | None, current: dict) -> dict:
                # Keep the broker's real source time separately: source_timestamp_ns
                # may be normalized to the pending bucket anchor for pairing, and
                # must not erase ordering information for subsequent ticks.
                current = dict(current)
                try:
                    current["_quote_source_timestamp_ns"] = int(
                        current.get("_quote_source_timestamp_ns")
                        or current.get("source_timestamp_ns")
                        or 0
                    )
                except (TypeError, ValueError, OverflowError):
                    current["_quote_source_timestamp_ns"] = 0
                if previous is None:
                    return current
                previous_bid = self._price(previous.get("bid"))
                previous_ask = self._price(previous.get("ask"))
                previous_ltp = self._price(previous.get("ltp") or previous.get("last_traded_price"))
                current_bid = self._price(current.get("bid"))
                current_ask = self._price(current.get("ask"))
                age_ns = time.monotonic_ns() - int(
                    bucket.get(
                        "quote_wait_started_ns",
                        bucket.get("first_seen_monotonic_ns", time.monotonic_ns()),
                    )
                )
                if (
                    (previous_bid is None or previous_ask is None)
                    and previous_ltp is not None
                    and current_bid is not None
                    and current_ask is not None
                    and age_ns < 3_000_000_000
                ):
                    anchored = dict(current)
                    anchored["source_timestamp_ns"] = previous.get("source_timestamp_ns")
                    return anchored
                return current

            accepted_observation = True

            def update_leg_without_time_regression(previous: dict | None, current: dict) -> dict | None:
                nonlocal accepted_observation
                if previous is None:
                    return preserve_pending_anchor(previous, current)
                try:
                    previous_ns = int(
                        previous.get("_quote_source_timestamp_ns")
                        or previous.get("source_timestamp_ns")
                        or 0
                    )
                    current_ns = int(
                        current.get("_quote_source_timestamp_ns")
                        or current.get("source_timestamp_ns")
                        or 0
                    )
                except (TypeError, ValueError, OverflowError):
                    previous_ns = current_ns = 0
                if previous_ns > current_ns > 0:
                    self._count("out_of_order_leg_updates")
                    accepted_observation = False
                    return previous
                return preserve_pending_anchor(previous, current)

            if option_type in {"CE", "PE"} and strike is not None:
                legs = bucket["options"].setdefault(strike, {})
                updated = update_leg_without_time_regression(legs.get(option_type), payload)
                if updated is not None:
                    legs[option_type] = updated
            else:
                # Future instruments are accepted through one explicit path only.
                # The upfront identity guard requires FUT in the symbol, so an
                # empty option_type is a legacy future representation, not a
                # blanket permission for arbitrary cash/index quotes.
                updated = update_leg_without_time_regression(bucket.get("future"), payload)
                if updated is not None:
                    bucket["future"] = updated

            # Rejected out-of-order ticks must not refresh observation health:
            # the cached quote did not advance even though transport received a payload.
            if accepted_observation:
                with self._diagnostics_lock:
                    self._last_observation_at_ns = time.time_ns()

            if bucket["future"] is None:
                self._count("missing_future_leg")
                self._prune(timestamp_ns)
                return ()
            future_payload = bucket["future"]
            try:
                future_expiry = self._expiry(future_payload.get("expiry"))
            except ValueError:
                self._count("invalid_future_expiry")
                self._prune(timestamp_ns)
                return ()
            future_timestamp_ns = int(future_payload.get("source_timestamp_ns") or 0)
            if future_timestamp_ns <= 0:
                self._count("invalid_future_timestamp")
                self._prune(timestamp_ns)
                return ()
            try:
                raw_future_lot_size = float(future_payload.get("lot_size") or 0)
                if (
                    not math.isfinite(raw_future_lot_size)
                    or raw_future_lot_size <= 0
                    or not raw_future_lot_size.is_integer()
                ):
                    raise ValueError("future lot size must be a positive integer")
                future_lot_size = int(raw_future_lot_size)
            except (TypeError, ValueError, OverflowError):
                self._count("invalid_future_lot_size")
                self._prune(timestamp_ns)
                return ()
            # Validate each strike independently. An incomplete or delayed option
            # at another strike must not block an otherwise synchronized CE+PE+FUT
            # triplet. The pure scanner requires one snapshot timestamp, so accepted
            # quotes are normalized to the future tick only after this guard passes.
            anchor_timestamp_ns = future_timestamp_ns
            strikes = bucket["options"]
            option_quotes: list[OptionQuote] = []
            for option_strike, legs in strikes.items():
                ce, pe = legs.get("CE"), legs.get("PE")
                if ce is None or pe is None:
                    self._count("missing_ce_or_pe_leg")
                    continue
                try:
                    ce_timestamp_ns = int(ce.get("source_timestamp_ns") or 0)
                    pe_timestamp_ns = int(pe.get("source_timestamp_ns") or 0)
                except (TypeError, ValueError, OverflowError):
                    self._count("invalid_option_timestamp")
                    continue
                if (
                    ce_timestamp_ns <= 0
                    or pe_timestamp_ns <= 0
                    or abs(ce_timestamp_ns - pe_timestamp_ns) > self.TIMESTAMP_TOLERANCE_NS
                    or abs(ce_timestamp_ns - future_timestamp_ns) > self.TIMESTAMP_TOLERANCE_NS
                    or abs(pe_timestamp_ns - future_timestamp_ns) > self.TIMESTAMP_TOLERANCE_NS
                ):
                    self._count("timestamp_mismatch")
                    continue
                # Never combine legs from a different expiry with the future.
                try:
                    matching_expiry = (
                        self._expiry(ce.get("expiry")) == future_expiry
                        and self._expiry(pe.get("expiry")) == future_expiry
                    )
                except ValueError:
                    matching_expiry = False
                if not matching_expiry:
                    self._count("expiry_mismatch")
                    continue
                if not liquid_leg(ce) or not liquid_leg(pe):
                    self._count("illiquid_or_incomplete_option_leg")
                    continue
                ce_bid, ce_ask = self._price(ce.get("bid")), self._price(ce.get("ask"))
                pe_bid, pe_ask = self._price(pe.get("bid")), self._price(pe.get("ask"))
                if None in (ce_bid, ce_ask, pe_bid, pe_ask):
                    continue
                # The contract lot must agree across CE, PE and FUT. Falling
                # back to one leg's lot can silently misstate executable P&L.
                def contract_lot(leg: dict) -> int | None:
                    try:
                        raw = float(leg.get("lot_size") or 0)
                    except (TypeError, ValueError, OverflowError):
                        return None
                    if raw != raw or raw in (float("inf"), float("-inf")):
                        return None
                    if raw <= 0 or not raw.is_integer():
                        return None
                    return int(raw)

                ce_lot = contract_lot(ce)
                pe_lot = contract_lot(pe)
                future_lot = contract_lot(future_payload)
                if ce_lot is None or pe_lot is None or future_lot is None:
                    self._count("invalid_option_lot_size")
                    continue
                if ce_lot != pe_lot or ce_lot != future_lot:
                    self._count("lot_size_mismatch")
                    continue
                lot = ce_lot
                option_quotes.append(
                    OptionQuote(
                        timestamp_ns=anchor_timestamp_ns,
                        underlying=symbol,
                        expiry=future_expiry,
                        strike=option_strike,
                        call_bid=ce_bid,
                        call_ask=ce_ask,
                        put_bid=pe_bid,
                        put_ask=pe_ask,
                        lot_size=lot,
                        instrument_class=cls,
                        volume=nonnegative_int(ce.get("volume")),
                        oi=nonnegative_int(ce.get("oi")),
                    )
                )

            quote_wait_started_ns = bucket.get("quote_wait_started_ns")
            bucket_age_ns = time.monotonic_ns() - int(
                quote_wait_started_ns
                if quote_wait_started_ns is not None
                else bucket.get("first_seen_monotonic_ns", time.monotonic_ns())
            )
            if not option_quotes:
                # LTP-complete CE+PE legs form a candidate even if one executable
                # book is late. Keep it in memory for at most three seconds so a
                # follow-up quote tick can complete the same candidate.
                pending_quote_pair = any(
                    legs.get("CE") is not None
                    and legs.get("PE") is not None
                    and self._price(legs["CE"].get("ltp") or legs["CE"].get("last_traded_price")) is not None
                    and self._price(legs["PE"].get("ltp") or legs["PE"].get("last_traded_price")) is not None
                    and (
                        not liquid_leg(legs["CE"])
                        or not liquid_leg(legs["PE"])
                    )
                    for legs in strikes.values()
                )
                if pending_quote_pair:
                    if bucket.get("quote_wait_started_ns") is None:
                        bucket["quote_wait_started_ns"] = time.monotonic_ns()
                        bucket_age_ns = 0
                    if bucket_age_ns < 3_000_000_000:
                        self._count("waiting_for_executable_quote")
                        return ()
                self._count("no_executable_ce_pe_pair")
                self._prune(timestamp_ns)
                return ()

            future_bid, future_ask = self._price(future_payload.get("bid")), self._price(future_payload.get("ask"))
            if future_bid is None or future_ask is None or future_ask < future_bid:
                if self._price(future_payload.get("ltp") or future_payload.get("last_traded_price")) is not None:
                    if bucket.get("quote_wait_started_ns") is None:
                        bucket["quote_wait_started_ns"] = time.monotonic_ns()
                        bucket_age_ns = 0
                    if bucket_age_ns < 3_000_000_000:
                        self._count("waiting_for_executable_quote")
                        return ()
                self._count("invalid_future_quote")
                self._prune(timestamp_ns)
                return ()
            future = FutureQuote(
                timestamp_ns=anchor_timestamp_ns,
                underlying=symbol,
                expiry=future_expiry,
                bid=future_bid,
                ask=future_ask,
                lot_size=future_lot_size,
                instrument_class=cls,
                volume=nonnegative_int(future_payload.get("volume")),
                oi=nonnegative_int(future_payload.get("oi")),
            )
            try:
                atm = self.atm_provider(symbol, timestamp_ns)
            except Exception:
                self._count("atm_provider_errors")
                self._prune(timestamp_ns)
                return ()
            if atm is None:
                self._count("missing_atm_price")
                self._prune(timestamp_ns)
                return ()
            try:
                config = self.config_provider(symbol)
            except Exception:
                self._count("config_provider_errors")
                self._prune(timestamp_ns)
                return ()
            if not isinstance(config, SyntheticScanConfig):
                self._count("invalid_scanner_config")
                self._prune(timestamp_ns)
                return ()
            # Derive carry horizon from this contract's actual expiry, not a static default.
            expiry_text = str(future.expiry or "").strip().upper()
            expiry_date = None
            for expiry_format in ("%Y%m%d", "%d%b%Y", "%d%b%y", "%Y-%m-%d"):
                try:
                    expiry_date = datetime.strptime(expiry_text, expiry_format).date()
                    break
                except ValueError:
                    continue
            if expiry_date is None:
                self._count("invalid_expiry_for_pricing")
                self._prune(timestamp_ns)
                return ()
            tz = ZoneInfo("Asia/Kolkata")
            expiry_at = datetime.combine(expiry_date, datetime_time(15, 30), tzinfo=tz)
            time_to_expiry_years = max(0.0, (expiry_at - datetime.now(tz)).total_seconds() / (365.25 * 24 * 60 * 60))
            config = replace(config, time_to_expiry_years=time_to_expiry_years)
            try:
                atm_strike = float(atm)
                if not math.isfinite(atm_strike) or atm_strike <= 0:
                    raise ValueError("ATM strike must be a finite positive number")
                results = scan_synthetic_snapshot(
                    option_quotes,
                    future,
                    atm_strike=atm_strike,
                    config=config,
                )
            except Exception:
                self._count("scan_calculation_errors")
                self._prune(timestamp_ns)
                return ()
            self._prune(timestamp_ns)
            self._count("scan_evaluations")
            # observe() already holds _lock while evaluating this complete snapshot.
            self._last_successful_calculation_at_ns = time.time_ns()
            self._last_result_count = len(results)
            self._count("successful_pairs", len(option_quotes))
            if results:
                self._count("opportunities_emitted", len(results))
            else:
                self._count("empty_calculations")
        if results and self.on_result is not None:
            try:
                self.on_result(results)
            except Exception:
                # A downstream alert/storage callback must not break the feed consumer.
                self._count("result_callback_errors")
        return results

    def pair_monitor_snapshot(self, limit: int = 200) -> dict:
        """Expose recent sanitized option/future combinations, including incomplete legs."""
        now_ns = time.time_ns()
        rows = []
        with self._lock:
            self._prune(max((key[1] for key in self._buckets), default=0))
            for (symbol, bucket_ts, expiry), bucket in self._buckets.items():
                if bucket_ts >= 1_000_000_000_000_000 and now_ns - bucket_ts > 5_000_000_000:
                    continue
                future = bucket.get("future") or {}
                try:
                    future_expiry = self._expiry(future.get("expiry")) if future.get("expiry") else expiry
                except ValueError:
                    future_expiry = None
                for strike, legs in bucket.get("options", {}).items():
                    ce, pe = legs.get("CE") or {}, legs.get("PE") or {}
                    missing = []
                    if future and future_expiry != expiry:
                        missing.append("EXPIRY_MISMATCH")
                    if not future:
                        missing.append("MISSING_FUTURE")
                    if not ce:
                        missing.append("MISSING_CALL")
                    if not pe:
                        missing.append("MISSING_PUT")
                    if future and ce and pe:
                        try:
                            stamps = [int(x.get("source_timestamp_ns") or 0) for x in (future, ce, pe)]
                            if min(stamps) <= 0:
                                missing.append("INVALID_TIMESTAMP")
                            elif max(stamps) - min(stamps) > self.TIMESTAMP_TOLERANCE_NS:
                                missing.append("TIMESTAMP_MISMATCH")
                        except (TypeError, ValueError, OverflowError):
                            missing.append("INVALID_TIMESTAMP")
                    try:
                        leg_timestamps = [
                            int(x.get("source_timestamp_ns") or 0)
                            for x in (future, ce, pe)
                        ]
                    except (TypeError, ValueError, OverflowError):
                        leg_timestamps = []
                        if "INVALID_TIMESTAMP" not in missing:
                            missing.append("INVALID_TIMESTAMP")
                    latest_timestamp = (
                        max(leg_timestamps)
                        if leg_timestamps and max(leg_timestamps) > 0
                        else bucket_ts
                    )

                    def positive(value):
                        try:
                            number = float(value)
                            return number > 0
                        except (TypeError, ValueError, OverflowError):
                            return False

                    def executable(leg):
                        bid, ask = self._price(leg.get("bid")), self._price(leg.get("ask"))
                        return bid is not None and ask is not None and ask >= bid

                    if future and ce and pe:
                        try:
                            ce_expiry = self._expiry(ce.get("expiry"))
                            pe_expiry = self._expiry(pe.get("expiry"))
                            if (ce_expiry != expiry or pe_expiry != expiry or future_expiry != expiry) and "EXPIRY_MISMATCH" not in missing:
                                missing.append("EXPIRY_MISMATCH")
                        except ValueError:
                            missing.append("INVALID_EXPIRY")
                        if not executable(future):
                            missing.append("INVALID_FUTURE_QUOTE")
                        for label, option in (("CALL", ce), ("PUT", pe)):
                            if not executable(option):
                                missing.append(f"INVALID_{label}_QUOTE")
                            if not positive(option.get("bid_qty")) or not positive(option.get("ask_qty")):
                                missing.append(f"{label}_DEPTH_MISSING")
                            # Match scanner eligibility: executable two-sided depth is
                            # required, but missing volume/OI metadata alone is not a
                            # blocker. Reporting it as incomplete would contradict live results.
                        def monitor_lot(value):
                            try:
                                number = float(value or 0)
                            except (TypeError, ValueError, OverflowError):
                                return None
                            if not math.isfinite(number) or number <= 0 or not number.is_integer():
                                return None
                            return int(number)

                        ce_lot = monitor_lot(ce.get("lot_size"))
                        pe_lot = monitor_lot(pe.get("lot_size"))
                        future_lot = monitor_lot(future.get("lot_size"))
                        if ce_lot is None or pe_lot is None or future_lot is None:
                            missing.append("INVALID_LOT_SIZE")
                        elif ce_lot != pe_lot or ce_lot != future_lot:
                            missing.append("LOT_SIZE_MISMATCH")
                        try:
                            monitor_atm = self.atm_provider(symbol, latest_timestamp)
                        except Exception:
                            self._count("monitor_atm_provider_errors")
                            monitor_atm = None
                        try:
                            monitor_atm_number = float(monitor_atm)
                        except (TypeError, ValueError, OverflowError):
                            monitor_atm_number = 0.0
                        if not math.isfinite(monitor_atm_number) or monitor_atm_number <= 0:
                            missing.append("MISSING_ATM")
                        if not missing:
                            missing.append("AWAITING_SCANNER_QUALIFICATION")
                    rows.append({
                        "timestamp_ns": latest_timestamp,
                        "underlying": symbol,
                        "expiry": expiry,
                        "strike": strike,
                        "future_bid": self._price(future.get("bid")),
                        "future_ask": self._price(future.get("ask")),
                        "call_bid": self._price(ce.get("bid")),
                        "call_ask": self._price(ce.get("ask")),
                        "put_bid": self._price(pe.get("bid")),
                        "put_ask": self._price(pe.get("ask")),
                        "lot_size": ce.get("lot_size") or future.get("lot_size"),
                        "state": "READY_TO_EVALUATE" if missing == ["AWAITING_SCANNER_QUALIFICATION"] else "INCOMPLETE",
                        "reason": ",".join(missing) if missing else "INCOMPLETE",
                        # Pair monitor reports structural readiness only. It is not
                        # the scanner's emitted opportunity/result stream.
                        "readiness_scope": "STRUCTURAL_ONLY",
                        "opportunity_confirmed": False,
                        "qualification_status": "NOT_CONFIRMED",
                    })
        rows.sort(key=lambda row: int(row.get("timestamp_ns") or 0), reverse=True)
        return {"status": "success", "data": rows[:max(1, min(int(limit), 500))]}

    TIMESTAMP_TOLERANCE_NS = 1_000_000_000

    def _matching_bucket_key(self, symbol: str, timestamp_ns: int, expiry: int) -> tuple[str, int, int]:
        # Normal ticks may join only a timestamp-compatible snapshot. Extending
        # matching to three seconds is reserved for a bucket explicitly waiting
        # for executable quotes; otherwise unrelated one-second snapshots can be
        # collapsed into one bucket and valid newer legs can be blocked by stale legs.
        compatible = [
            key
            for key in self._buckets
            if key[0] == symbol
            and key[2] == expiry
            and abs(key[1] - timestamp_ns) <= self.TIMESTAMP_TOLERANCE_NS
        ]
        if compatible:
            return min(compatible, key=lambda key: abs(key[1] - timestamp_ns))

        now_monotonic_ns = time.monotonic_ns()
        pending = []
        for key, bucket in self._buckets.items():
            if key[0] != symbol or key[2] != expiry:
                continue
            if abs(key[1] - timestamp_ns) > 3_000_000_000:
                continue
            started_ns = bucket.get("quote_wait_started_ns")
            if started_ns is None:
                continue
            try:
                wait_age_ns = now_monotonic_ns - int(started_ns)
            except (TypeError, ValueError, OverflowError):
                continue
            if 0 <= wait_age_ns < 3_000_000_000:
                pending.append(key)
        if pending:
            return min(pending, key=lambda key: abs(key[1] - timestamp_ns))
        return (symbol, timestamp_ns, expiry)

    def _prune(self, timestamp_ns: int) -> None:
        # A delayed/out-of-order tick must not move the cleanup watermark
        # backwards and keep older buckets alive indefinitely.
        newest_bucket_ns = max((key[1] for key in self._buckets), default=timestamp_ns)
        watermark_ns = max(timestamp_ns, newest_bucket_ns)
        cutoff = watermark_ns - self.MAX_BUCKET_AGE_NS
        now_monotonic_ns = time.monotonic_ns()
        before = len(self._buckets)

        def bucket_is_fresh(bucket: dict) -> bool:
            if not isinstance(bucket, dict) or bucket.get("first_seen_monotonic_ns") is None:
                # Keep compatibility with manually seeded diagnostic/test buckets;
                # production buckets always receive a monotonic creation timestamp.
                return True
            try:
                first_seen_ns = int(bucket["first_seen_monotonic_ns"])
            except (TypeError, ValueError, OverflowError):
                return False
            wait_started = bucket.get("quote_wait_started_ns")
            if wait_started is not None:
                try:
                    wait_age_ns = now_monotonic_ns - int(wait_started)
                except (TypeError, ValueError, OverflowError):
                    return False
                # A pending executable-book quote gets its own bounded three-second
                # grace period, even if the original bucket was created earlier.
                return 0 <= wait_age_ns < self.MAX_BUCKET_AGE_NS
            age_ns = now_monotonic_ns - first_seen_ns
            return 0 <= age_ns < self.MAX_BUCKET_AGE_NS

        self._buckets = {
            key: value for key, value in self._buckets.items()
            if key[1] >= cutoff and bucket_is_fresh(value)
        }
        expired = before - len(self._buckets)
        if expired:
            self._count("pruned_buckets", expired)
            self._count("monotonic_age_pruned_buckets", expired)

        # Time-based pruning is the primary bound; this hard cap protects the
        # process if a broker feed emits many distinct timestamps/symbol keys.
        if len(self._buckets) > self.MAX_ACTIVE_BUCKETS:
            excess = len(self._buckets) - self.MAX_ACTIVE_BUCKETS
            oldest = sorted(self._buckets, key=lambda key: key[1])[:excess]
            for key in oldest:
                self._buckets.pop(key, None)
            self._count("bucket_capacity_evictions", excess)


__all__ = ["LiveSyntheticScanner"]
