from datetime import datetime, timezone

import pytest

from app.backtesting.cash_future_coverage_audit import (
    audit_one_year_contract_coverage,
    one_year_market_dates,
    require_one_year_contract_coverage,
)


class FakeCatalog:
    def __init__(self, missing=None, snapshots=()):
        self.missing = set(missing or ())
        self.snapshots = tuple(snapshots)
        self.resolved = []

    def snapshot_dates(self):
        return self.snapshots

    def resolve(self, *, exchange, underlying, as_of, mode):
        self.resolved.append((as_of, mode))
        if (as_of, mode) in self.missing:
            raise LookupError(f"missing contract {as_of.isoformat()}")
        return object()


def test_one_year_market_dates_are_market_local_and_leap_safe():
    start, end = one_year_market_dates(
        end=datetime(2026, 9, 8, 0, 15, tzinfo=timezone.utc)
    )
    assert start == datetime(2025, 9, 7, tzinfo=timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date()
    assert end == datetime(2026, 9, 8, tzinfo=timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date()


def test_one_year_audit_checks_both_legs_and_reports_trading_days():
    from datetime import date

    catalog = FakeCatalog(snapshots=(date(2025, 9, 1),))
    audit = audit_one_year_contract_coverage(
        catalog,
        exchange="NFO",
        underlying="ABC",
        end=datetime(2026, 9, 8, 12, 0),
        mode="BOTH",
    )
    assert audit.start == date(2025, 9, 8)
    assert audit.end == date(2026, 9, 8)
    assert audit.modes == ("CURRENT", "NEAR")
    assert audit.checked_trading_days > 0
    assert audit.complete
    assert len(catalog.resolved) == audit.checked_trading_days * 2


def test_one_year_audit_fails_closed_for_missing_snapshot():
    with pytest.raises(LookupError, match="one-year Cash-Future contract coverage incomplete"):
        require_one_year_contract_coverage(
            FakeCatalog(snapshots=()),
            exchange="NFO",
            underlying="ABC",
            end=datetime(2026, 9, 8, 12, 0),
            mode="CURRENT",
        )
