"""Low-latency live synthetic Cash-Carry scanner.

Consumes the normalized one-second observations emitted by the live synthetic
recorder. It assembles real CE+PE+future quotes at the same timestamp and then
delegates pricing/ranking to the existing pure synthetic scanner.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from threading import Lock
import time
from typing import Callable

from app.backtesting.arbitrage_backtester import FutureQuote, OptionQuote
from app.scanner.synthetic_cash_carry import SyntheticScanConfig, SyntheticScanResult, scan_synthetic_snapshot


class LiveSyntheticScanner:
    """Bounded in-memory assembler for one-second synthetic opportunities."""

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
        self._last_observation_at_ns: int | None = None
        self._last_successful_calculation_at_ns: int | None = None
        self._last_result_count = 0
        self._buckets: dict[tuple[str, int, int], dict] = {}

    def _count(self, reason: str, amount: int = 1) -> None:
        with self._diagnostics_lock:
            self._diagnostics[reason] += amount

    def diagnostics_snapshot(self) -> dict:
        """Return scanner-local counters without exposing cached market payloads."""
        # Keep lock order consistent with observe(): scanner state before counters.
        with self._lock:
            with self._diagnostics_lock:
                counters = dict(self._diagnostics)
            counters["active_buckets"] = len(self._buckets)
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
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def observe(self, payload: dict) -> tuple[SyntheticScanResult, ...]:
        self._count("observations_received")
        observed_at_ns = time.time_ns()
        with self._lock:
            self._last_observation_at_ns = observed_at_ns
        if not isinstance(payload, dict):
            self._count("invalid_payload_or_scope")
            return ()
        symbol = str(payload.get("underlying") or "").strip().upper()
        cls = str(payload.get("instrument_class") or "").strip().upper()
        option_type = str(payload.get("option_type") or "").strip().upper()
        try:
            timestamp_ns = int(payload.get("source_timestamp_ns") or 0)
        except (TypeError, ValueError, OverflowError):
            self._count("invalid_timestamp")
            return ()
        if timestamp_ns <= 0:
            self._count("invalid_timestamp")
            return ()
        # Epoch-nanosecond timestamps are checked against wall time. Small logical
        # timestamps remain supported by deterministic unit tests only.
        if timestamp_ns >= 1_000_000_000_000_000:
            now_ns = time.time_ns()
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
                return max(float(value), 0.0)
            except (TypeError, ValueError):
                return 0.0

        def liquid_leg(option: dict) -> bool:
            # Liquidity is evaluated from live ticks, not the static instrument master.
            return (
                self._price(option.get("bid")) is not None
                and self._price(option.get("ask")) is not None
                and self._price(option.get("ask")) >= self._price(option.get("bid"))
                and positive_number(option.get("bid_qty")) > 0
                and positive_number(option.get("ask_qty")) > 0
                and (
                    positive_number(option.get("volume")) > 0
                    or positive_number(option.get("oi")) > 0
                )
            )

        try:
            expiry = self._expiry(payload.get("expiry"))
        except ValueError:
            self._count("invalid_expiry")
            return ()
        strike = self._price(payload.get("strike"))
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

            if option_type in {"CE", "PE"} and strike is not None:
                legs = bucket["options"].setdefault(strike, {})
                payload = preserve_pending_anchor(legs.get(option_type), payload)
                legs[option_type] = payload
            elif option_type == "":
                bucket["future"] = preserve_pending_anchor(bucket.get("future"), payload)
            else:
                # Futures may not carry option_type in older instrument masters.
                instrument_symbol = str(payload.get("symbol") or "").upper()
                if "FUT" in instrument_symbol:
                    bucket["future"] = preserve_pending_anchor(bucket.get("future"), payload)

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
                lot = int(float(ce.get("lot_size") or future_payload.get("lot_size") or 0))
                if lot <= 0:
                    self._count("invalid_option_lot_size")
                    continue
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
                        volume=int(float(ce.get("volume") or 0)),
                        oi=int(float(ce.get("oi") or 0)),
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
                lot_size=int(float(future_payload.get("lot_size") or 0)),
                instrument_class=cls,
                volume=int(float(future_payload.get("volume") or 0)),
                oi=int(float(future_payload.get("oi") or 0)),
            )
            if future.lot_size <= 0:
                self._count("invalid_future_lot_size")
                self._prune(timestamp_ns)
                return ()
            atm = self.atm_provider(symbol, timestamp_ns)
            if atm is None:
                self._count("missing_atm_price")
                self._prune(timestamp_ns)
                return ()
            config = self.config_provider(symbol)
            results = scan_synthetic_snapshot(
                option_quotes,
                future,
                atm_strike=float(atm),
                config=config,
            )
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
            self.on_result(results)
        return results

    TIMESTAMP_TOLERANCE_NS = 1_000_000_000

    def _matching_bucket_key(self, symbol: str, timestamp_ns: int, expiry: int) -> tuple[str, int, int]:
        # Keep expiry isolated and allow a delayed executable-book tick to rejoin
        # its LTP candidate for up to three seconds. The per-leg timestamp guard
        # still rejects unrelated or genuinely mismatched legs.
        candidates = [
            key
            for key in self._buckets
            if key[0] == symbol
            and key[2] == expiry
            and abs(key[1] - timestamp_ns) <= 3_000_000_000
        ]
        if candidates:
            return min(candidates, key=lambda key: abs(key[1] - timestamp_ns))
        return (symbol, timestamp_ns, expiry)

    def _prune(self, timestamp_ns: int) -> None:
        cutoff = timestamp_ns - 3_000_000_000
        self._buckets = {key: value for key, value in self._buckets.items() if key[1] >= cutoff}


__all__ = ["LiveSyntheticScanner"]
