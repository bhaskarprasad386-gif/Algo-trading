"""Coverage validation for persisted Cash-Future historical observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.scanner.cash_future_history import CashFutureHistoryPoint


@dataclass(frozen=True)
class CashFutureContractCoverage:
    symbol: str
    contract_month: str
    count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    expected_count: int | None
    missing_timestamps: tuple[datetime, ...]

    @property
    def complete(self) -> bool:
        return self.count > 0 and not self.missing_timestamps and (
            self.expected_count is None or self.count == self.expected_count
        )


@dataclass(frozen=True)
class CashFutureCoverageReport:
    contracts: tuple[CashFutureContractCoverage, ...]

    @property
    def complete(self) -> bool:
        return bool(self.contracts) and all(contract.complete for contract in self.contracts)

    @property
    def status(self) -> str:
        return "READY" if self.complete else "INCOMPLETE"


def build_cash_future_coverage_report(
    points: Iterable[CashFutureHistoryPoint],
    *,
    expected_timestamps: Iterable[datetime] | None = None,
) -> CashFutureCoverageReport:
    """Validate persisted history by symbol/contract without mixing expiries.

    When expected timestamps are supplied they are authoritative: coverage is
    checked independently for every symbol/contract pair. Without them the
    report remains a descriptive coverage report and does not invent cadence
    assumptions across trading-session boundaries.
    """
    grouped: dict[tuple[str, str], list[CashFutureHistoryPoint]] = {}
    for point in points:
        grouped.setdefault((point.symbol.upper(), point.contract_month), []).append(point)

    expected = tuple(sorted(set(expected_timestamps or ()))) if expected_timestamps is not None else None
    reports: list[CashFutureContractCoverage] = []
    for (symbol, contract_month), group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda point: point.timestamp)
        observed = {point.timestamp for point in ordered}
        missing = tuple(timestamp for timestamp in expected if timestamp not in observed) if expected is not None else ()
        reports.append(
            CashFutureContractCoverage(
                symbol=symbol,
                contract_month=contract_month,
                count=len(ordered),
                first_timestamp=ordered[0].timestamp,
                last_timestamp=ordered[-1].timestamp,
                expected_count=len(expected) if expected is not None else None,
                missing_timestamps=missing,
            )
        )

    return CashFutureCoverageReport(tuple(reports))


__all__ = ["CashFutureContractCoverage", "CashFutureCoverageReport", "build_cash_future_coverage_report"]
