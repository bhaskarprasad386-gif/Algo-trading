from datetime import datetime

import pytest

from app.backtesting.cash_future_one_year_coverage import CashFutureOneYearCoverageAudit

from .test_cash_future_contract_preflight import FakeCatalog


def test_one_year_audit_uses_market_timezone_and_calendar_lookback():
    end = datetime(2026, 9, 8, 0, 30)  # 06:00 IST
    audit = CashFutureOneYearCoverageAudit(FakeCatalog()).audit(
        exchange="NFO", underlying="ABC", end=end, mode="CURRENT"
    )
    assert audit.start.date().isoformat() == "2025-09-08"
    assert audit.end.date().isoformat() == "2026-09-08"
    assert audit.contract.checked_days == 366
    assert audit.contract.modes == ("CURRENT",)


def test_one_year_audit_is_fail_closed_when_snapshots_are_missing():
    audit = CashFutureOneYearCoverageAudit(FakeCatalog(snapshots=())).audit(
        exchange="NFO", underlying="ABC", end=datetime(2026, 9, 8), mode="BOTH"
    )
    assert not audit.complete
    assert audit.missing_snapshot_dates
    assert not audit.missing_contract_dates


def test_one_year_require_complete_exposes_actionable_failure():
    with pytest.raises(LookupError, match="one-year Cash-Future historical coverage incomplete"):
        CashFutureOneYearCoverageAudit(FakeCatalog(snapshots=())).require_complete(
            exchange="NFO", underlying="ABC", end=datetime(2026, 9, 8), mode="BOTH"
        )
