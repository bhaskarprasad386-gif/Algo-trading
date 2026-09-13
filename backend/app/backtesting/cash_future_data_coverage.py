"""Read-only data coverage audit for Cash-Future backtesting."""

from __future__ import annotations

from dataclasses import dataclass

from .cash_future_download_report import (
    CashFutureDownloadProgressReport,
    CashFutureDownloadReporter,
)
from .cash_future_download_queue import CashFutureDownloadQueue
from .historical_catalog import HistoricalCatalog
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class CashFutureDataCoverageReport:
    mode: str
    report: CashFutureDownloadProgressReport

    def _chunks(self):
        return (*self.report.spot, *(chunk for chunks in self.report.futures for chunk in chunks))

    @property
    def complete(self) -> bool:
        """Return True only when every reported spot/future chunk is complete."""
        return all(chunk.complete for chunk in self._chunks())

    @property
    def total_chunks(self) -> int:
        return len(self._chunks())

    @property
    def complete_chunks(self) -> int:
        return sum(chunk.complete for chunk in self._chunks())

    @property
    def incomplete_chunks(self) -> int:
        return self.total_chunks - self.complete_chunks

    @property
    def missing_timestamps(self) -> int:
        return sum(chunk.missing for chunk in self._chunks())


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
    ) -> CashFutureDataCoverageReport:
        report = self.reporter.report(
            queue=queue,
            mode=mode,
            interval_ns=interval_ns,
            spot_sessions=spot_sessions,
            future_sessions=future_sessions,
        )
        return CashFutureDataCoverageReport(mode=mode, report=report)

    def require_complete(self, **kwargs) -> CashFutureDataCoverageReport:
        audit = self.audit(**kwargs)
        if not audit.complete:
            raise LookupError(
                "Cash-Future historical data coverage incomplete: "
                f"{audit.incomplete_chunks} incomplete chunks, "
                f"{audit.missing_timestamps} missing timestamps"
            )
        return audit
