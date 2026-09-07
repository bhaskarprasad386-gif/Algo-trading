"""Read-only data coverage audit for Cash-Future backtesting."""

from __future__ import annotations

from dataclasses import dataclass

from .cash_future_download_report import CashFutureDownloadProgressReport, CashFutureDownloadReporter
from .cash_future_download_queue import CashFutureDownloadQueue
from .session_gap_planner import SessionWindow
from .historical_catalog import HistoricalCatalog


@dataclass(frozen=True)
class CashFutureDataCoverageAudit:
    mode: str
    report: CashFutureDownloadProgressReport

    @property
    def complete(self) -> bool:
        return self.report.complete

    @property
    def total_chunks(self) -> int:
        return self.report.total_chunks

    @property
    def complete_chunks(self) -> int:
        return self.report.complete_chunks

    @property
    def incomplete_chunks(self) -> int:
        return self.report.incomplete_chunks

    @property
    def missing_timestamps(self) -> int:
        return self.report.spot.missing_timestamps + sum(
            item.missing_timestamps for item in self.report.futures
        )


class CashFutureDataCoverageAudit:
    """Audit stored Cash/Future bars without downloading or modifying data."""

    def __init__(self, catalog: HistoricalCatalog) -> None:
        self.reporter = CashFutureDownloadReporter(catalog)

    def audit(
        self,
        *,
        queue: CashFutureDownloadQueue,
        mode: str,
        interval_ns: int,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]] | None = None,
    ) -> CashFutureDataCoverageAudit:
        report = self.reporter.report(
            queue=queue,
            mode=mode,
            interval_ns=interval_ns,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
        )
        return CashFutureDataCoverageAudit(mode=mode, report=report)

    def require_complete(self, **kwargs) -> CashFutureDataCoverageAudit:
        audit = self.audit(**kwargs)
        if not audit.complete:
            raise LookupError(
                "Cash-Future historical data coverage incomplete: "
                f"{audit.incomplete_chunks} incomplete chunks, "
                f"{audit.missing_timestamps} missing timestamps"
            )
        return audit
