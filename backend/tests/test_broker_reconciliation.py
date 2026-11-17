import pytest

from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerSnapshot,
    BrokerTradeSnapshot,
    ExpectedOrder,
    ReconciliationStatus,
    reconcile_snapshot,
)


def _expected():
    return (ExpectedOrder("call-buy", "ord-1", "NIFTY-CE", "BUY", 50),)


def _order(*, order_id="ord-1", symbol="NIFTY-CE", side="BUY", quantity=50, filled=25):
    return BrokerOrderSnapshot(order_id, symbol, side, quantity, filled)


def _trade(*, trade_id="trade-1", order_id="ord-1", quantity=25, fill_price=None, occurred_at_ns=None):
    return BrokerTradeSnapshot(trade_id, order_id, quantity, fill_price, occurred_at_ns)


def _snapshot(orders=(), trades=(), *, complete=True, account="acct-1", broker="angel_one"):
    return BrokerSnapshot(account, broker, tuple(orders), tuple(trades), complete)


def test_complete_matching_snapshot_is_consistent_but_not_trade_authorization():
    report = reconcile_snapshot(
        account_id="acct-1", broker="ANGEL_ONE", expected_orders=_expected(),
        local_fill_quantities={"trade-1": 25},
        snapshot=_snapshot((_order(),), (_trade(),)),
    )
    assert report.is_consistent is True
    assert report.matched_order_count == 1
    assert report.broker == "angel_one"


def test_incomplete_snapshot_blocks_consistency_even_when_empty():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=(),
        local_fill_quantities={}, snapshot=_snapshot(complete=False),
    )
    assert report.is_consistent is False
    assert report.issues[0].status is ReconciliationStatus.INCOMPLETE_SNAPSHOT


def test_missing_order_and_unrecorded_trade_are_both_reported():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={}, snapshot=_snapshot(trades=(_trade(),)),
    )
    assert {x.status for x in report.issues} == {
        ReconciliationStatus.MISSING_BROKER_ORDER,
        ReconciliationStatus.TRADE_ORDER_MISSING,
        ReconciliationStatus.UNRECORDED_BROKER_TRADE,
    }


def test_order_identity_mismatch_blocks_consistency():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={}, snapshot=_snapshot((_order(side="SELL"),)),
    )
    assert report.is_consistent is False
    assert report.issues[0].status is ReconciliationStatus.BROKER_ORDER_MISMATCH


def test_local_fill_absence_and_quantity_mismatch_are_detected():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=(),
        local_fill_quantities={"missing": 10, "trade-1": 20},
        snapshot=_snapshot(trades=(_trade(quantity=25),)),
    )
    assert {x.status for x in report.issues} == {
        ReconciliationStatus.LOCAL_FILL_MISSING_AT_BROKER,
        ReconciliationStatus.FILL_QUANTITY_MISMATCH,
        ReconciliationStatus.TRADE_ORDER_MISSING,
    }


def test_duplicate_broker_order_and_trade_ids_are_reported():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={"trade-1": 25},
        snapshot=_snapshot((_order(), _order()), (_trade(), _trade())),
    )
    assert {x.status for x in report.issues} == {
        ReconciliationStatus.DUPLICATE_BROKER_ORDER_ID,
        ReconciliationStatus.DUPLICATE_BROKER_TRADE_ID,
        ReconciliationStatus.ORDER_TRADE_FILL_TOTAL_MISMATCH,
    }


@pytest.mark.parametrize("snapshot", [
    _snapshot(account="acct-2"),
    _snapshot(broker="zerodha"),
])
def test_cross_account_or_broker_snapshot_is_rejected(snapshot):
    with pytest.raises(ValueError, match="scope"):
        reconcile_snapshot(
            account_id="acct-1", broker="angel_one", expected_orders=(),
            local_fill_quantities={}, snapshot=snapshot,
        )


def test_account_and_broker_scope_are_required():
    with pytest.raises(ValueError):
        reconcile_snapshot(
            account_id="", broker="angel_one", expected_orders=(),
            local_fill_quantities={}, snapshot=_snapshot(),
        )


def test_broker_order_filled_quantity_must_equal_trade_rows_total():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={"trade-1": 25},
        snapshot=_snapshot((_order(filled=30),), (_trade(quantity=25),)),
    )
    assert report.is_consistent is False
    assert ReconciliationStatus.ORDER_TRADE_FILL_TOTAL_MISMATCH in {
        issue.status for issue in report.issues
    }


def test_broker_trade_must_reference_an_order_in_the_snapshot():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=(),
        local_fill_quantities={"trade-1": 25},
        snapshot=_snapshot((), (_trade(order_id="unknown-order"),)),
    )
    assert report.is_consistent is False
    assert ReconciliationStatus.TRADE_ORDER_MISSING in {
        issue.status for issue in report.issues
    }



def test_fill_execution_price_and_timestamp_match_exactly():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={"trade-1": 25},
        local_fill_evidence={"trade-1": (25, "101.25", 123456789)},
        snapshot=_snapshot((_order(),), (_trade(fill_price=101.25, occurred_at_ns=123456789),)),
    )
    assert report.is_consistent is True


def test_fill_price_and_timestamp_mismatches_are_both_reported():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={"trade-1": 25},
        local_fill_evidence={"trade-1": (25, "101.25", 123456789)},
        snapshot=_snapshot((_order(),), (_trade(fill_price=101.5, occurred_at_ns=123456790),)),
    )
    assert {issue.status for issue in report.issues} == {
        ReconciliationStatus.FILL_PRICE_MISMATCH,
        ReconciliationStatus.FILL_TIMESTAMP_MISMATCH,
    }


@pytest.mark.parametrize(
    "trade",
    [
        _trade(fill_price=None, occurred_at_ns=123456789),
        _trade(fill_price=101.25, occurred_at_ns=None),
    ],
)
def test_fill_evidence_missing_in_broker_snapshot_is_reported(trade):
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={"trade-1": 25},
        local_fill_evidence={"trade-1": (25, "101.25", 123456789)},
        snapshot=_snapshot((_order(),), (trade,)),
    )
    assert ReconciliationStatus.FILL_EXECUTION_EVIDENCE_MISSING in {
        issue.status for issue in report.issues
    }


def test_fill_evidence_must_be_scoped_to_existing_local_fill_quantity():
    with pytest.raises(ValueError, match="matching local fill quantity"):
        reconcile_snapshot(
            account_id="acct-1", broker="angel_one", expected_orders=(),
            local_fill_quantities={},
            local_fill_evidence={"trade-1": (25, "101.25", 123456789)},
            snapshot=_snapshot(trades=(_trade(fill_price=101.25, occurred_at_ns=123456789),)),
        )
