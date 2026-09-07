"""Fail-closed historical Cash-Future contract coverage preflight."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ContractCoverageGap:
    date: date
    mode: str
    reason: str
    kind: str = "CONTRACT"


@dataclass(frozen=True)
class ContractCoverageReport:
    start: date
    end: date
    modes: tuple[str, ...]
    checked_days: int
    gaps: tuple[ContractCoverageGap, ...]

    @property
    def complete(self) -> bool:
        return not self.gaps

    @property
    def missing_snapshot_dates(self) -> tuple[date, ...]:
        return tuple(sorted({gap.date for gap in self.gaps if gap.kind == "SNAPSHOT"}))

    @property
    def missing_contract_dates(self) -> tuple[date, ...]:
        return tuple(sorted({gap.date for gap in self.gaps if gap.kind == "CONTRACT"}))


class CashFutureContractPreflight:
    """Validate historical contract identity before any market-data request starts."""

    def __init__(self, contract_catalog) -> None:
        self.contract_catalog = contract_catalog

    @staticmethod
    def _modes(mode: str) -> tuple[str, ...]:
        normalized = mode.upper()
        if normalized == "BOTH":
            return ("CURRENT", "NEAR")
        if normalized in {"CURRENT", "NEAR"}:
            return (normalized,)
        raise ValueError("mode must be CURRENT, NEAR or BOTH")

    def check(self, *, exchange: str, underlying: str, start: date, end: date, mode: str = "BOTH") -> ContractCoverageReport:
        if end < start:
            raise ValueError("end must not precede start")
        modes = self._modes(mode)
        snapshots = tuple(self.contract_catalog.snapshot_dates())
        gaps: list[ContractCoverageGap] = []
        checked = 0
        cursor = start
        while cursor <= end:
            checked += 1
            snapshot_available = any(snapshot <= cursor for snapshot in snapshots)
            for leg in modes:
                if not snapshot_available:
                    gaps.append(ContractCoverageGap(
                        cursor,
                        leg,
                        f"no historical contract-master snapshot for {cursor.isoformat()}",
                        "SNAPSHOT",
                    ))
                    continue
                try:
                    self.contract_catalog.resolve(exchange=exchange, underlying=underlying, as_of=cursor, mode=leg)
                except (LookupError, ValueError) as exc:
                    gaps.append(ContractCoverageGap(cursor, leg, str(exc), "CONTRACT"))
            cursor += timedelta(days=1)
        return ContractCoverageReport(start, end, modes, checked, tuple(gaps))

    def require_complete(self, **kwargs) -> ContractCoverageReport:
        report = self.check(**kwargs)
        if not report.complete:
            sample = "; ".join(
                f"{gap.date.isoformat()} {gap.mode} [{gap.kind}]: {gap.reason}"
                for gap in report.gaps[:5]
            )
            suffix = "" if len(report.gaps) <= 5 else f"; +{len(report.gaps) - 5} more"
            raise LookupError(f"historical Cash-Future contract coverage incomplete: {sample}{suffix}")
        return report
