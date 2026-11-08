from datetime import date

import pytest

from app.scanner.iv_skew_flip.basis import (
    BasisHistory,
    calculate_basis,
    resolve_index_future_contracts,
)


def test_resolve_nearest_distinct_index_future_expiries():
    rows = [
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "NIFTY", "expiry": "26NOV2026", "token": "102", "symbol": "NIFTY26NOV26FUT"},
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "NIFTY", "expiry": "26DEC2026", "token": "103", "symbol": "NIFTY26DEC26FUT"},
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "NIFTY", "expiry": "29OCT2026", "token": "101", "symbol": "NIFTY29OCT26FUT"},
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "name": "NIFTY", "expiry": "26NOV2026", "token": "104", "symbol": "NOT-INDEX-FUT"},
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "BANKNIFTY", "expiry": "26NOV2026", "token": "105", "symbol": "BANKNIFTY26NOV26FUT"},
    ]
    result = resolve_index_future_contracts(rows, underlying="NIFTY", today=date(2026, 11, 1))
    assert [item.token for item in result] == ["102", "103"]
    assert [item.expiry.isoformat() for item in result] == ["2026-11-26", "2026-12-26"]


def test_future_resolver_fails_closed_for_missing_or_duplicate_contracts():
    with pytest.raises(LookupError, match="no valid"):
        resolve_index_future_contracts([], underlying="NIFTY", today=date(2026, 11, 1))
    rows = [
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "NIFTY", "expiry": "26NOV2026", "token": "102", "symbol": "A"},
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "NIFTY", "expiry": "26NOV2026", "token": "103", "symbol": "B"},
    ]
    with pytest.raises(LookupError, match="duplicate"):
        resolve_index_future_contracts(rows, underlying="NIFTY", today=date(2026, 11, 1))


@pytest.mark.parametrize(
    ("spot_ts", "future_ts", "expected"),
    [
        (1_000_000_000_000, 1_000_000_000_500, "LIVE"),
        (996_000_000_000, 1_000_000_000_000, "STALE"),
        (1_000_000_000_000, 1_003_000_000_000, "STALE"),
        (1_001_000_000_000, 1_000_000_000_000, "INVALID_TIMESTAMP"),
    ],
)
def test_calculate_basis_enforces_freshness_and_pair_skew(spot_ts, future_ts, expected):
    result = calculate_basis(
        spot=24000, future=24060,
        spot_timestamp_ns=spot_ts, future_timestamp_ns=future_ts,
        now_ns=1_000_000_000_000,
    )
    assert result["status"] == expected
    assert result["basis_points"] == 60
    assert result["basis_pct"] == 0.25
    assert result["signals_allowed"] is (expected == "LIVE")


@pytest.mark.parametrize("bad", [None, True, 0, -1, float("nan"), float("inf"), "bad"])
def test_calculate_basis_rejects_invalid_prices(bad):
    with pytest.raises(ValueError):
        calculate_basis(
            spot=bad, future=24060,
            spot_timestamp_ns=1_000_000_000_000,
            future_timestamp_ns=1_000_000_000_000,
            now_ns=1_000_000_000_000,
        )


def test_basis_history_ignores_stale_and_out_of_order_observations():
    history = BasisHistory()
    base = 1_800_000_000_000_000_000
    assert not history.add({"fresh": False})
    assert history.add({
        "fresh": True, "timestamp_ns": base, "spot": 24000,
        "future": 24050, "basis_points": 50, "basis_pct": 50 / 24000 * 100,
    })
    assert not history.add({
        "fresh": True, "timestamp_ns": base, "spot": 24000,
        "future": 24055, "basis_points": 55, "basis_pct": 55 / 24000 * 100,
    })
    assert history.add({
        "fresh": True, "timestamp_ns": base + 60_000_000_000, "spot": 24010,
        "future": 24070, "basis_points": 60, "basis_pct": 60 / 24010 * 100,
    })
    result = history.snapshot(base + 60_000_000_000)
    assert result["trend"] == "WIDENING"
    assert result["windows"]["1m"]["basis_change_points"] == 10
    assert result["sample_count"] == 2
