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
        with self._diagnostics_lock:
            counters = dict(self._diagnostics)
        with self._lock:
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
        if bid is None or ask is None or ask < bid:
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
            bucket = self._buckets.setdefault(key, {"future": None, "options": {}})
            if option_type in {"CE", "PE"} and strike is not None:
                bucket["options"].setdefault(strike, {})[option_type] = payload
            elif option_type == "":
                bucket["future"] = payload
            else:
                # Futures may not carry option_type in older instrument masters.
                instrument_symbol = str(payload.get("symbol") or "").upper()
                if "FUT" in instrument_symbol:
                    bucket["future"] = payload

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
            timestamps = [int(future_payload.get("source_timestamp_ns") or 0)]
            for legs in bucket["options"].values():
                timestamps.extend(int(leg.get("source_timestamp_ns") or 0) for leg in legs.values())
            timestamps = [value for value in timestamps if value > 0]
            if not timestamps or max(timestamps) - min(timestamps) > self.TIMESTAMP_TOLERANCE_NS:
                self._count("timestamp_mismatch")
                self._prune(timestamp_ns)
                return ()
            anchor_timestamp_ns = max(timestamps)
            strikes = bucket["options"]
            option_quotes: list[OptionQuote] = []
            for option_strike, legs in strikes.items():
                ce, pe = legs.get("CE"), legs.get("PE")
                if ce is None or pe is None:
                    self._count("missing_ce_or_pe_leg")
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

            if not option_quotes:
                self._count("no_executable_ce_pe_pair")
                self._prune(timestamp_ns)
                return ()

            future_bid, future_ask = self._price(future_payload.get("bid")), self._price(future_payload.get("ask"))
            if future_bid is None or future_ask is None or future_ask < future_bid:
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
            with self._lock:
                self._last_successful_calculation_at_ns = time.time_ns()
                self._last_result_count = len(results)
            if results:
                self._count("opportunities_emitted", len(results))
                self._count("successful_pairs", len(option_quotes))
            else:
                self._count("empty_calculations")
        if results and self.on_result is not None:
            self.on_result(results)
        return results

    TIMESTAMP_TOLERANCE_NS = 1_000_000_000

    def _matching_bucket_key(self, symbol: str, timestamp_ns: int, expiry: int) -> tuple[str, int, int]:
        # Keep expiry isolated, but allow synchronized ticks to straddle a
        # wall-clock second boundary. The later timestamp validation remains
        # the final guard against pairing genuinely stale legs.
        candidates = [
            key
            for key in self._buckets
            if key[0] == symbol
            and key[2] == expiry
            and abs(key[1] - timestamp_ns) <= self.TIMESTAMP_TOLERANCE_NS
        ]
        if candidates:
            return min(candidates, key=lambda key: abs(key[1] - timestamp_ns))
        return (symbol, timestamp_ns, expiry)

    def _prune(self, timestamp_ns: int) -> None:
        cutoff = timestamp_ns - 3_000_000_000
        self._buckets = {key: value for key, value in self._buckets.items() if key[1] >= cutoff}


__all__ = ["LiveSyntheticScanner"]
