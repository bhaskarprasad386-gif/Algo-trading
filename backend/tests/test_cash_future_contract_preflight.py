from datetime import date

import pytest

from app.backtesting.cash_future_contract_preflight import CashFutureContractPreflight


class FakeCatalog:
    def __init__(self, missing=None):
        self.missing = set(missing or ())

    def resolve(self, *, exchange, underlying, as_of, mode):
        if (as_of, mode) in self.missing:
            raise LookupError(f"no historical contract for {as_of.isoformat()}")
        return object()


def test_preflight_checks_both_legs_for_every_day():
    report = CashFutureContractPreflight(FakeCatalog()).check(
        exchange="NFO", underlying="ABC", start=date(2026, 1, 1), end=date(2026, 1, 3), mode="BOTH"
    )
    assert report.checked_days == 3
    assert report.modes == ("CURRENT", "NEAR")
    assert report.complete


def test_preflight_reports_missing_historical_identity():
    missing_day = date(2026, 1, 2)
    report = CashFutureContractPreflight(FakeCatalog({(missing_day, "NEAR")})).check(
        exchange="NFO", underlying="ABC", start=date(2026, 1, 1), end=date(2026, 1, 3), mode="BOTH"
    )
    assert not report.complete
    assert len(report.gaps) == 1
    assert report.gaps[0].date == missing_day
    assert report.gaps[0].mode == "NEAR"


def test_preflight_fails_closed_with_actionable_error():
    with pytest.raises(LookupError, match="historical Cash-Future contract coverage incomplete"):
        CashFutureContractPreflight(FakeCatalog({(date(2026, 1, 2), "CURRENT")})).require_complete(
            exchange="NFO", underlying="ABC", start=date(2026, 1, 1), end=date(2026, 1, 3), mode="CURRENT"
        )
