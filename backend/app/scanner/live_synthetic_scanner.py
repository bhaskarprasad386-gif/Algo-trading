"""Low-latency live synthetic Cash-Carry scanner.

Consumes the normalized one-second observations emitted by the live synthetic
recorder. It assembles real CE+PE+future quotes at the same timestamp and then
delegates pricing/ranking to the existing pure synthetic scanner.
"""

from __future__ import annotations

from datetime import datetime
from threading import Lock
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
        self._buckets: dict[tuple[str, int], dict] = {}

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
        symbol = str(payload.get("underlying") or "").strip().upper()
        cls = str(payload.get("instrument_class") or "").strip().upper()
        option_type = str(payload.get("option_type") or "").strip().upper()
        timestamp_ns = int(payload.get("source_timestamp_ns") or 0)
        if not symbol or cls not in {"STOCK", "INDEX"} or timestamp_ns <= 0:
            return ()
        bid = self._price(payload.get("bid"))
        ask = self._price(payload.get("ask"))
        if bid is None or ask is None or ask < bid:
            return ()

        expiry = self._expiry(payload.get("expiry"))
        strike = self._price(payload.get("strike"))
        key = (symbol, timestamp_ns)
        with self._lock:
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
                self._prune(timestamp_ns)
                return ()
            future_payload = bucket["future"]
            strikes = bucket["options"]
            option_quotes: list[OptionQuote] = []
            for option_strike, legs in strikes.items():
                ce, pe = legs.get("CE"), legs.get("PE")
                if ce is None or pe is None:
                    continue
                ce_bid, ce_ask = self._price(ce.get("bid")), self._price(ce.get("ask"))
                pe_bid, pe_ask = self._price(pe.get("bid")), self._price(pe.get("ask"))
                if None in (ce_bid, ce_ask, pe_bid, pe_ask):
                    continue
                lot = int(float(ce.get("lot_size") or future_payload.get("lot_size") or 0))
                if lot <= 0:
                    continue
                option_quotes.append(
                    OptionQuote(
                        timestamp_ns=timestamp_ns,
                        underlying=symbol,
                        expiry=expiry,
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
                self._prune(timestamp_ns)
                return ()

            future_bid, future_ask = self._price(future_payload.get("bid")), self._price(future_payload.get("ask"))
            if future_bid is None or future_ask is None:
                self._prune(timestamp_ns)
                return ()
            future = FutureQuote(
                timestamp_ns=timestamp_ns,
                underlying=symbol,
                expiry=expiry,
                bid=future_bid,
                ask=future_ask,
                lot_size=int(float(future_payload.get("lot_size") or 0)),
                instrument_class=cls,
                volume=int(float(future_payload.get("volume") or 0)),
                oi=int(float(future_payload.get("oi") or 0)),
            )
            if future.lot_size <= 0:
                self._prune(timestamp_ns)
                return ()
            atm = self.atm_provider(symbol, timestamp_ns)
            if atm is None:
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
        if results and self.on_result is not None:
            self.on_result(results)
        return results

    def _prune(self, timestamp_ns: int) -> None:
        cutoff = timestamp_ns - 3_000_000_000
        self._buckets = {key: value for key, value in self._buckets.items() if key[1] >= cutoff}


__all__ = ["LiveSyntheticScanner"]
