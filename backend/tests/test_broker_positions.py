from datetime import date, datetime, timedelta, timezone

import pytest

from app.execution.broker_positions import (
    BrokerPositionSnapshot,
    PositionKey,
    PositionQuantity,
    PositionReconciliationStatus,
    reconcile_positions,
)


TODAY = date(2026, 11, 18)
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
KEY = PositionKey("NFO", "NIFTY26NOV25000CE", "NRML")


def _snapshot(positions=(), **overrides):
    values = dict(
        account_id="acct-1",
        broker="angel_one",
        trading_date=TODAY,
        fetched_at=NOW,
        positions=tuple(positions),
        complete=True,
    )
    values.update(overrides)
    return BrokerPositionSnapshot(**values)


def _reconcile(snapshot, expected=()):
    return reconcile_positions(
        account_id="acct-1",
        broker="ANGEL_ONE",
        trading_date=TODAY,
        expected_positions=tuple(expected),
        snapshot=snapshot,
        now=NOW,
    )


def test_complete_empty_snapshot_can_confirm_flat_account():
    report = _reconcile(_snapshot())
    assert report.is_consistent_read_only
    assert report.issues == ()


def test_incomplete_empty_snapshot_is_never_treated_as_flat():
    report = _reconcile(_snapshot(complete=False))
    assert not report.is_consistent_read_only
    assert PositionReconciliationStatus.INCOMPLETE_SNAPSHOT in {
        issue.status for issue in report.issues
    }


def test_complete_matching_net_position_is_consistent():
    expected = PositionQuantity(KEY, 50)
    report = _reconcile(_snapshot((expected,)), (expected,))
    assert report.is_consistent_read_only


def test_untracked_broker_exposure_blocks_reconciliation():
    report = _reconcile(_snapshot((PositionQuantity(KEY, -50),)))
    assert PositionReconciliationStatus.UNTRACKED_BROKER_EXPOSURE in {
        issue.status for issue in report.issues
    }


def test_local_exposure_missing_from_broker_blocks_reconciliation():
    report = _reconcile(_snapshot(), (PositionQuantity(KEY, 50),))
    assert PositionReconciliationStatus.LOCAL_EXPOSURE_MISSING_AT_BROKER in {
        issue.status for issue in report.issues
    }


def test_net_quantity_mismatch_blocks_reconciliation():
    report = _reconcile(
        _snapshot((PositionQuantity(KEY, 40),)),
        (PositionQuantity(KEY, 50),),
    )
    assert PositionReconciliationStatus.NET_QUANTITY_MISMATCH in {
        issue.status for issue in report.issues
    }


def test_stale_future_wrong_account_and_wrong_date_all_block():
    stale = _snapshot(fetched_at=NOW - timedelta(seconds=31))
    future = _snapshot(fetched_at=NOW + timedelta(seconds=1))
    wrong_account = _snapshot(account_id="acct-2")
    wrong_date = _snapshot(trading_date=date(2026, 11, 17))
    for snapshot, expected_status in (
        (stale, PositionReconciliationStatus.STALE_SNAPSHOT),
        (future, PositionReconciliationStatus.FUTURE_SNAPSHOT),
        (wrong_account, PositionReconciliationStatus.ACCOUNT_MISMATCH),
        (wrong_date, PositionReconciliationStatus.TRADING_DATE_MISMATCH),
    ):
        report = _reconcile(snapshot)
        assert expected_status in {issue.status for issue in report.issues}
        assert not report.is_consistent_read_only


def test_pagination_with_more_pages_blocks_even_if_provider_claims_complete_is_invalid():
    with pytest.raises(ValueError, match="more pages"):
        _snapshot(complete=True, has_more=True)


def test_incomplete_paginated_snapshot_is_blocked():
    report = _reconcile(_snapshot(complete=False, has_more=True))
    assert PositionReconciliationStatus.PAGINATION_INCOMPLETE in {
        issue.status for issue in report.issues
    }


def test_duplicate_position_rows_are_reported():
    report = _reconcile(_snapshot((PositionQuantity(KEY, 10), PositionQuantity(KEY, 10))))
    assert PositionReconciliationStatus.DUPLICATE_POSITION in {
        issue.status for issue in report.issues
    }


def test_position_key_requires_exchange_instrument_product_and_currency():
    with pytest.raises(ValueError):
        PositionKey("", "NIFTY", "NRML")
