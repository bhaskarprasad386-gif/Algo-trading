"""One-year Cash-Future historical coverage audit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .cash_future_contract_preflight import CashFutureContractPreflight, ContractCoverageReport
from .historical_download_plan import one_year_range

MARKET_TZ = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class OneYearCoverageAudit:
    start_ns: int
    end_ns: int
    contract: ContractCoverageReport

    @property
    def complete(self) -> bool:
        return self.contract.complete

    @property
    def start(self) -> datetime:
        return datetime.fromtimestamp(self.start_ns / 1_000_000_000, tz=timezone.utc).astimezone(MARKET_TZ)

    @property
    def end(self) -> datetime:
        return datetime.fromtimestamp(self.end_ns / 1_000_000_000, tz=timezone.utc).astimezone(MARKET_TZ)

    @property
    def missing_snapshot_dates(self):
        return self.contract.missing_snapshot_dates

    @property
    def missing_contract_dates(self):
        return self.contract.missing_contract_dates


class CashFutureOneYearCoverageAudit:
    """Audit one calendar year of historical contract identity before downloading data."""

    def __init__(self, contract_catalog) -> None:
        self.preflight = CashFutureContractPreflight(contract_catalog)

    def audit(self, *, exchange: str, underlying: str, end: datetime, mode: str = "BOTH") -> OneYearCoverageAudit:
        if end.tzinfo is None:
            end = end.replace(tzinfo=MARKET_TZ)
        start_ns, end_ns = one_year_range(end=end)
        start = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc).astimezone(MARKET_TZ).date()
        finish = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc).astimezone(MARKET_TZ).date()
        report = self.preflight.check(
            exchange=exchange,
            underlying=underlying,
            start=start,
            end=finish,
            mode=mode,
        )
        return OneYearCoverageAudit(start_ns=start_ns, end_ns=end_ns, contract=report)

    def require_complete(self, **kwargs) -> OneYearCoverageAudit:
        audit = self.audit(**kwargs)
        if not audit.complete:
            sample = "; ".join(
                f"{gap.date.isoformat()} {gap.mode} [{gap.kind}]: {gap.reason}"
                for gap in audit.contract.gaps[:5]
            )
            suffix = "" if len(audit.contract.gaps) <= 5 else f"; +{len(audit.contract.gaps) - 5} more"
            raise LookupError(f"one-year Cash-Future historical coverage incomplete: {sample}{suffix}")
        return audit
