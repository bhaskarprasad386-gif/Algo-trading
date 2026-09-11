"""Streaming data-quality gates for persisted Cash-Future history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .historical_catalog import HistoricalRecord


@dataclass(frozen=True)
class CashFutureDataQualityReport:
    records_checked: int
    invalid_ohlc: int = 0
    crossed_quotes: int = 0
    negative_depth: int = 0
    invalid_prices: int = 0
    duplicate_timestamps: int = 0

    @property
    def clean(self) -> bool:
        return not any(
            (
                self.invalid_ohlc,
                self.crossed_quotes,
                self.negative_depth,
                self.invalid_prices,
                self.duplicate_timestamps,
            )
        )

    @property
    def issue_count(self) -> int:
        return (
            self.invalid_ohlc
            + self.crossed_quotes
            + self.negative_depth
            + self.invalid_prices
            + self.duplicate_timestamps
        )

    def require_clean(self) -> "CashFutureDataQualityReport":
        if not self.clean:
            raise ValueError(
                "Cash-Future historical data-quality gate failed: "
                f"{self.issue_count} issues "
                f"(ohlc={self.invalid_ohlc}, crossed_quotes={self.crossed_quotes}, "
                f"negative_depth={self.negative_depth}, invalid_prices={self.invalid_prices}, "
                f"duplicate_timestamps={self.duplicate_timestamps})"
            )
        return self


def _audit_payload_stream(
    records: Iterable[tuple[tuple[object, ...], Mapping[str, object]]],
) -> CashFutureDataQualityReport:
    checked = invalid_ohlc = crossed_quotes = negative_depth = invalid_prices = duplicate_timestamps = 0
    seen: set[tuple[object, ...]] = set()

    for identity, payload in records:
        checked += 1
        if identity in seen:
            duplicate_timestamps += 1
        else:
            seen.add(identity)

        prices = [payload.get(name) for name in ("open", "high", "low", "close")]
        numeric_prices = [float(value) for value in prices if isinstance(value, (int, float))]
        if numeric_prices and any(value <= 0 for value in numeric_prices):
            invalid_prices += 1
        if all(isinstance(value, (int, float)) for value in prices):
            opened, high, low, close = (float(value) for value in prices)
            if low > high or not (low <= opened <= high) or not (low <= close <= high):
                invalid_ohlc += 1

        for bid_name, ask_name in (
            ("bid", "ask"),
            ("cash_bid", "cash_ask"),
            ("future_bid", "future_ask"),
        ):
            bid, ask = payload.get(bid_name), payload.get(ask_name)
            if isinstance(bid, (int, float)) and isinstance(ask, (int, float)):
                if float(bid) <= 0 or float(ask) <= 0:
                    invalid_prices += 1
                elif float(bid) > float(ask):
                    crossed_quotes += 1

        for name in (
            "bid_qty",
            "ask_qty",
            "cash_bid_qty",
            "cash_ask_qty",
            "future_bid_qty",
            "future_ask_qty",
        ):
            value = payload.get(name)
            if isinstance(value, (int, float)) and float(value) < 0:
                negative_depth += 1

        for name in ("cash_price", "future_price"):
            value = payload.get(name)
            if isinstance(value, (int, float)) and float(value) <= 0:
                invalid_prices += 1

    return CashFutureDataQualityReport(
        records_checked=checked,
        invalid_ohlc=invalid_ohlc,
        crossed_quotes=crossed_quotes,
        negative_depth=negative_depth,
        invalid_prices=invalid_prices,
        duplicate_timestamps=duplicate_timestamps,
    )


def audit_cash_future_records(records: Iterable[HistoricalRecord]) -> CashFutureDataQualityReport:
    """Audit a HistoricalRecord stream without materializing the full history."""
    return _audit_payload_stream((record.identity(), record.payload) for record in records)


def audit_cash_future_points(records: Iterable[object]) -> CashFutureDataQualityReport:
    """Audit persisted CashFutureHistoryPoint-like records as a stream."""
    def stream():
        for point in records:
            identity = (
                getattr(point, "symbol"),
                getattr(point, "contract_month"),
                getattr(point, "timestamp"),
            )
            payload = {
                "cash_price": getattr(point, "cash_price"),
                "future_price": getattr(point, "future_price"),
                "cash_bid": getattr(point, "cash_bid"),
                "cash_ask": getattr(point, "cash_ask"),
                "future_bid": getattr(point, "future_bid"),
                "future_ask": getattr(point, "future_ask"),
                "cash_bid_qty": getattr(point, "cash_bid_qty"),
                "cash_ask_qty": getattr(point, "cash_ask_qty"),
                "future_bid_qty": getattr(point, "future_bid_qty"),
                "future_ask_qty": getattr(point, "future_ask_qty"),
            }
            yield identity, payload

    return _audit_payload_stream(stream())


__all__ = [
    "CashFutureDataQualityReport",
    "audit_cash_future_records",
    "audit_cash_future_points",
]
