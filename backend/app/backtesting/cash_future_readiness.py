"""Combined fail-closed readiness gate for Cash-Future backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .cash_future_data_coverage import CashFutureDataCoverageAudit, CashFutureDataCoverageReport
from .cash_future_download_queue import CashFutureDownloadQueue
from .cash_future_one_year_coverage import CashFutureOneYearCoverageAudit, OneYearCoverageAudit


@dataclass(frozen=True)
class CashFutureReadinessReport:
    contract: OneYearCoverageAudit
    data: CashFutureDataCoverageReport

    @property
    def complete(self) -> bool:
        return self.contract.complete and self.data.complete

    @property
    def missing_snapshot_dates(self):
        return self.contract.missing_snapshot_dates

    @property
    def missing_contract_dates(self):
        return self.contract.missing_contract_dates

    @property
    def missing_timestamps(self) -> int:
        return self.data.missing_timestamps


class CashFutureReadinessGate:
    """Require both historical contract identity and stored bar completeness."""

    def __init__(self, *, contract_catalog, historical_catalog) -> None:
        self.contract_audit = CashFutureOneYearCoverageAudit(contract_catalog)
        self.data_audit = CashFutureDataCoverageAudit(historical_catalog)

    def audit(
        self,
        *,
        exchange: str,
        underlying: str,
        end: datetime,
        queue: CashFutureDownloadQueue,
        mode: str,
        interval_ns: int,
        spot_sessions,
        future_sessions=None,
    ) -> CashFutureReadinessReport:
        contract = self.contract_audit.audit(
            exchange=exchange, underlying=underlying, end=end, mode=mode
        )
        data = self.data_audit.audit(
            queue=queue,
            mode=mode,
            interval_ns=interval_ns,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
        )
        return CashFutureReadinessReport(contract=contract, data=data)

    def require_complete(self, **kwargs) -> CashFutureReadinessReport:
        report = self.audit(**kwargs)
        if report.complete:
            return report

        reasons: list[str] = []
        if not report.contract.complete:
            reasons.append(
                f"contract gaps={len(report.contract.contract.gaps)} "
                f"(snapshots={len(report.missing_snapshot_dates)}, "
                f"contracts={len(report.missing_contract_dates)})"
            )
        if not report.data.complete:
            reasons.append(
                f"data gaps={report.data.incomplete_chunks} incomplete chunks, "
                f"{report.missing_timestamps} missing timestamps"
            )
        raise LookupError("Cash-Future backtest readiness incomplete: " + "; ".join(reasons))
