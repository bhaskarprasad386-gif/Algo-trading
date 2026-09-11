from datetime import date

import pytest

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


def test_year_boundary_is_contiguous_and_non_overlapping():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2025, 12, 1), [
        ContractRecord("NFO", "SBIN25DECFUT", "901", date(2025, 12, 25), "STOCK_FUTURE", "SBIN", 750),
    ])
    catalog.upsert_snapshot(date(2025, 12, 26), [
        ContractRecord("NFO", "SBIN26JANFUT", "902", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
    ])
    segments = build_rollover_segments(catalog, exchange="NFO", underlying="SBIN",
                                       start=date(2025, 12, 30), end=date(2026, 1, 2), mode="CURRENT")
    assert [(s.start, s.end, s.future.token) for s in segments] == [
        (date(2025, 12, 30), date(2026, 1, 2), "902"),
    ]
    for left, right in zip(segments, segments[1:]):
        assert left.end < right.start


def test_expiry_transition_uses_next_contract_when_historical_snapshot_changes():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBIN26JANFUT", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBIN26FEBFUT", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    catalog.upsert_snapshot(date(2026, 1, 30), [
        ContractRecord("NFO", "SBIN26FEBFUT", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBIN26MARFUT", "103", date(2026, 3, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    segments = build_rollover_segments(catalog, exchange="NFO", underlying="SBIN",
                                       start=date(2026, 1, 28), end=date(2026, 1, 31), mode="CURRENT")
    assert [(s.start, s.end, s.future.token) for s in segments] == [
        (date(2026, 1, 28), date(2026, 1, 29), "101"),
        (date(2026, 1, 30), date(2026, 1, 31), "102"),
    ]


def test_missing_historical_snapshot_fails_closed():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBIN26JANFUT", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
    ])
    with pytest.raises(LookupError):
        build_rollover_segments(catalog, exchange="NFO", underlying="SBIN",
                                start=date(2025, 12, 30), end=date(2026, 1, 2), mode="CURRENT")


def test_both_builds_independent_current_and_near_legs():
    result = build_mode_segments(_catalog(), exchange="NFO", underlying="SBIN",
                                 start=date(2026, 1, 1), end=date(2026, 1, 2), mode="BOTH")
    assert len(result) == 2
    assert result[0][0].future.token == "101"
    assert result[1][0].future.token == "102"


def test_non_consecutive_session_days_extend_same_contract_without_calendar_gap():
    sessions = (date(2026, 1, 29), date(2026, 2, 2), date(2026, 2, 3))
    segments = build_rollover_segments(_catalog(), exchange="NFO", underlying="SBIN",
                                       start=date(2026, 1, 29), end=date(2026, 2, 3),
                                       mode="CURRENT", session_days=sessions)
    assert [(s.start, s.end, s.future.token) for s in segments] == [
        (date(2026, 1, 29), date(2026, 1, 29), "101"),
        (date(2026, 2, 2), date(2026, 2, 3), "102"),
    ]


def test_snapshot_rollover_can_be_non_adjacent_in_calendar_but_contiguous_in_sessions():
    sessions = (date(2026, 1, 29), date(2026, 1, 30), date(2026, 2, 2))
    segments = build_rollover_segments(_catalog(), exchange="NFO", underlying="SBIN",
                                       start=date(2026, 1, 29), end=date(2026, 2, 2),
                                       mode="CURRENT", session_days=sessions)
    assert [(s.start, s.end, s.future.token) for s in segments] == [
        (date(2026, 1, 29), date(2026, 1, 29), "101"),
        (date(2026, 1, 30), date(2026, 2, 2), "102"),
    ]


def test_same_contract_across_non_consecutive_sessions_is_one_continuous_segment():
    sessions = (
        date(2026, 1, 29),
        date(2026, 2, 2),
        date(2026, 2, 3),
    )
    segments = build_rollover_segments(
        _catalog(),
        exchange="NFO",
        underlying="SBIN",
        start=date(2026, 1, 29),
        end=date(2026, 2, 3),
        mode="CURRENT",
        session_days=sessions,
    )
    assert len(segments) == 2
    assert segments[0].future.token == "101"
    assert segments[1].future.token == "102"
    assert segments[0].end < segments[1].start
