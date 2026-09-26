from datetime import date

import pytest

from app.backtesting.cash_future_contract_preflight import CashFutureContractPreflight


class FakeCatalog:
    def __init__(self, missing=None, snapshots=(date(2025, 12, 1),)):
        self.missing = set(missing or ())
        self.snapshots = tuple(snapshots)
        self.resolved_dates = []

    def snapshot_dates(self):
        return self.snapshots

    def resolve(self, *, exchange, underlying, as_of, mode):
        self.resolved_dates.append(as_of)
        if (as_of, mode) in self.missing:
            raise LookupError(f"no historical contract for {as_of.isoformat()}")
        return object()


def test_preflight_checks_both_legs_for_every_trading_day():
    catalog = FakeCatalog()
    report = CashFutureContractPreflight(catalog).check(
        exchange="NFO", underlying="ABC", start=date(2026, 1, 1), end=date(2026, 1, 3), mode="BOTH"
    )
    assert report.checked_days == 3
    assert report.checked_trading_days == 2
    assert report.modes == ("CURRENT", "NEAR")
    assert report.complete
    assert catalog.resolved_dates == [date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 2)]


def test_preflight_reports_missing_historical_identity():
    missing_day = date(2026, 1, 2)
    report = CashFutureContractPreflight(FakeCatalog({(missing_day, "NEAR")})).check(
        exchange="NFO", underlying="ABC", start=date(2026, 1, 1), end=date(2026, 1, 3), mode="BOTH"
    )
    assert not report.complete
    assert len(report.gaps) == 1
    assert report.gaps[0].date == missing_day
    assert report.gaps[0].mode == "NEAR"
    assert report.gaps[0].kind == "CONTRACT"
    assert report.missing_contract_dates == (missing_day,)
    assert report.missing_snapshot_dates == ()


def test_preflight_classifies_missing_snapshot_before_resolve():
    report = CashFutureContractPreflight(FakeCatalog(snapshots=())).check(
        exchange="NFO", underlying="ABC", start=date(2025, 1, 1), end=date(2025, 1, 2), mode="BOTH"
    )
    assert len(report.gaps) == 4
    assert all(gap.kind == "SNAPSHOT" for gap in report.gaps)
    assert report.missing_snapshot_dates == (date(2025, 1, 1), date(2025, 1, 2))
    assert report.missing_contract_dates == ()


def test_preflight_fails_closed_with_actionable_error():
    with pytest.raises(LookupError, match="historical Cash-Future contract coverage incomplete"):
        CashFutureContractPreflight(FakeCatalog({(date(2026, 1, 2), "CURRENT")})).require_complete(
            exchange="NFO", underlying="ABC", start=date(2026, 1, 1), end=date(2026, 1, 3), mode="CURRENT"
        )


def test_preflight_ignores_nse_fno_holiday():
    catalog = FakeCatalog()
    report = CashFutureContractPreflight(catalog).check(
        exchange="NFO", underlying="ABC", start=date(2026, 1, 26), end=date(2026, 1, 27), mode="CURRENT"
    )
    assert report.checked_trading_days == 1
    assert report.complete
    assert catalog.resolved_dates == [date(2026, 1, 27)]


def test_preflight_rejects_unsupported_exchange_via_calendar():
    with pytest.raises(ValueError, match="unsupported NSE instrument exchange"):
        CashFutureContractPreflight(FakeCatalog()).check(
            exchange="BSE", underlying="ABC", start=date(2026, 1, 2), end=date(2026, 1, 2), mode="CURRENT"
        )


def test_preflight_uses_real_historical_catalog_snapshot_for_both_legs(tmp_path):
    from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord

    db = tmp_path / "contracts.sqlite3"
    with ContractMasterCatalog(str(db)) as catalog:
        catalog.upsert_snapshot(
            date(2026, 1, 1),
            (
                ContractRecord("NFO", "ABC26JANFUT", "1001", date(2026, 1, 29), "STOCK_FUTURE", "ABC", 100),
                ContractRecord("NFO", "ABC26FEBFUT", "1002", date(2026, 2, 26), "STOCK_FUTURE", "ABC", 100),
            ),
        )
        report = CashFutureContractPreflight(catalog).check(
            exchange="NFO",
            underlying="ABC",
            start=date(2026, 1, 1),
            end=date(2026, 1, 2),
            mode="BOTH",
        )

    assert report.complete
    assert report.checked_trading_days == 2
    assert report.gaps == ()


def test_preflight_with_real_catalog_stays_fail_closed_before_first_snapshot(tmp_path):
    from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord

    db = tmp_path / "contracts.sqlite3"
    with ContractMasterCatalog(str(db)) as catalog:
        catalog.upsert_snapshot(
            date(2026, 1, 5),
            (
                ContractRecord("NFO", "ABC26JANFUT", "1001", date(2026, 1, 29), "STOCK_FUTURE", "ABC", 100),
                ContractRecord("NFO", "ABC26FEBFUT", "1002", date(2026, 2, 26), "STOCK_FUTURE", "ABC", 100),
            ),
        )
        report = CashFutureContractPreflight(catalog).check(
            exchange="NFO",
            underlying="ABC",
            start=date(2026, 1, 2),
            end=date(2026, 1, 2),
            mode="CURRENT",
        )

    assert not report.complete
    assert report.gaps[0].kind == "SNAPSHOT"
    assert report.missing_snapshot_dates == (date(2026, 1, 2),)


def test_preflight_both_mode_crosses_expiry_into_distinct_current_near_contracts(tmp_path):
    from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord

    db = tmp_path / "contracts.sqlite3"
    with ContractMasterCatalog(str(db)) as catalog:
        catalog.upsert_snapshot(
            date(2026, 1, 1),
            (
                ContractRecord("NFO", "ABC26JANFUT", "1001", date(2026, 1, 29), "STOCK_FUTURE", "ABC", 100),
                ContractRecord("NFO", "ABC26FEBFUT", "1002", date(2026, 2, 26), "STOCK_FUTURE", "ABC", 100),
                ContractRecord("NFO", "ABC26MARFUT", "1003", date(2026, 3, 26), "STOCK_FUTURE", "ABC", 100),
            ),
        )
        before = (
            catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 1, 28), mode="CURRENT"),
            catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 1, 28), mode="NEAR"),
        )
        after = (
            catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 1, 30), mode="CURRENT"),
            catalog.resolve(exchange="NFO", underlying="ABC", as_of=date(2026, 1, 30), mode="NEAR"),
        )
        report = CashFutureContractPreflight(catalog).check(
            exchange="NFO", underlying="ABC",
            start=date(2026, 1, 28), end=date(2026, 1, 30), mode="BOTH",
        )

    assert report.complete
    assert before[0].token == "1001"
    assert before[1].token == "1002"
    assert after[0].token == "1002"
    assert after[1].token == "1003"
    assert before[0].token != before[1].token
    assert after[0].token != after[1].token
