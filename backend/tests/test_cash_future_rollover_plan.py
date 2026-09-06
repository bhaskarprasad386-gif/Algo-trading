from datetime import date

from app.backtesting.cash_future_rollover_plan import build_mode_segments, build_rollover_segments
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord


def _catalog():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBIN26JANFUT", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBIN26FEBFUT", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    catalog.upsert_snapshot(date(2026, 1, 30), [
        ContractRecord("NFO", "SBIN26FEBFUT", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBIN26MARFUT", "103", date(2026, 3, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    return catalog


def test_segments_change_identity_only_at_snapshot_boundary():
    segments = build_rollover_segments(_catalog(), exchange="NFO", underlying="SBIN",
                                       start=date(2026, 1, 29), end=date(2026, 2, 2), mode="CURRENT")
    assert [(s.start, s.end, s.future.token) for s in segments] == [
        (date(2026, 1, 29), date(2026, 1, 29), "101"),
        (date(2026, 1, 30), date(2026, 2, 2), "102"),
    ]


def test_both_builds_independent_current_and_near_legs():
    result = build_mode_segments(_catalog(), exchange="NFO", underlying="SBIN",
                                 start=date(2026, 1, 1), end=date(2026, 1, 2), mode="BOTH")
    assert len(result) == 2
    assert result[0][0].future.token == "101"
    assert result[1][0].future.token == "102"
