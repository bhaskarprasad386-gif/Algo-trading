"""One-year historical Cash-Future contract coverage audit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .cash_future_contract_preflight import CashFutureContractPreflight, ContractCoverageGap
from .historical_download_plan import one_year_range

MARKET_TZ = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class OneYearCashFutureCoverageAudit:
    start: date
    end: date
    modes: tuple[str, ...]
    checked_days: int
    checked_trading_days: int
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


def one_year_market_dates(*, end: datetime) -> tuple[date, date]:
    """Return the calendar dates covered by a one-year market-local lookback."""
    if end.tzinfo is None:
        end = end.replace(tzinfo=MARKET_TZ)
    end = end.astimezone(MARKET_TZ)
    start_ns, end_ns = one_year_range(end=end)
    start = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=MARKET_TZ).date()
    finish = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=MARKET_TZ).date()
    return start, finish


def audit_one_year_contract_coverage(
    contract_catalog,
    *,
    exchange: str,
    underlying: str,
    end: datetime,
    mode: str = "BOTH",
) -> OneYearCashFutureCoverageAudit:
    """Audit one year of historical contract identity without downloading data."""
    start, finish = one_year_market_dates(end=end)
    report = CashFutureContractPreflight(contract_catalog).check(
        exchange=exchange,
        underlying=underlying,
        start=start,
        end=finish,
        mode=mode,
    )
    return OneYearCashFutureCoverageAudit(
        start=report.start,
        end=report.end,
        modes=report.modes,
        checked_days=report.checked_days,
        checked_trading_days=report.checked_trading_days,
        gaps=report.gaps,
    )


def require_one_year_contract_coverage(
    contract_catalog,
    *,
    exchange: str,
    underlying: str,
    end: datetime,
    mode: str = "BOTH",
) -> OneYearCashFutureCoverageAudit:
    """Fail closed when one-year historical contract identity is incomplete."""
    audit = audit_one_year_contract_coverage(
        contract_catalog,
        exchange=exchange,
        underlying=underlying,
        end=end,
        mode=mode,
    )
    if not audit.complete:
        sample = "; ".join(
            f"{gap.date.isoformat()} {gap.mode} [{gap.kind}]: {gap.reason}"
            for gap in audit.gaps[:5]
        )
        suffix = "" if len(audit.gaps) <= 5 else f"; +{len(audit.gaps) - 5} more"
        raise LookupError(
            f"one-year Cash-Future contract coverage incomplete: {sample}{suffix}"
        )
    return audit
