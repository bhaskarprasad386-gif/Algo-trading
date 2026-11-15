import pytest

from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerTradeSnapshot,
    ExpectedOrder,
    ReconciliationStatus,
    reconcile_snapshot,
)


def _expected():
    return (ExpectedOrder("call-buy", "ord-1", "NIFTY-CE", "BUY", 50),)


def _order(*, order_id="ord-1", symbol="NIFTY-CE", side="BUY", quantity=50, filled=25):
    return BrokerOrderSnapshot(order_id, symbol, side, quantity, filled)


def _trade(*, trade_id="trade-1", order_id="ord-1", quantity=25):
    return BrokerTradeSnapshot(trade_id, order_id, quantity)


def test_complete_matching_snapshot_is_consistent_but_not_trade_authorization():
    report = reconcile_snapshot(
        account_id="acct-1", broker="ANGEL_ONE", expected_orders=_expected(),
        local_fill_quantities={"trade-1": 25}, broker_orders=(_order(),),
        broker_trades=(_trade(),), snapshot_complete=True,
    )
    assert report.safe_to_resume is True
    assert report.matched_order_count == 1
    assert report.broker == "angel_one"


def test_incomplete_snapshot_blocks_resume_even_when_empty():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=(),
        local_fill_quantities={}, broker_orders=(), broker_trades=(),
        snapshot_complete=False,
    )
    assert report.safe_to_resume is False
    assert report.issues[0].status is ReconciliationStatus.INCOMPLETE_SNAPSHOT


def test_missing_order_and_unrecorded_trade_are_both_reported():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={}, broker_orders=(),
        broker_trades=(_trade(),), snapshot_complete=True,
    )
    assert {x.status for x in report.issues} == {
        ReconciliationStatus.MISSING_BROKER_ORDER,
        ReconciliationStatus.UNRECORDED_BROKER_TRADE,
    }


def test_order_identity_mismatch_blocks_resume():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={}, broker_orders=(_order(side="SELL"),),
        broker_trades=(), snapshot_complete=True,
    )
    assert report.safe_to_resume is False
    assert report.issues[0].status is ReconciliationStatus.BROKER_ORDER_MISMATCH


def test_local_fill_absence_and_quantity_mismatch_are_detected():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=(),
        local_fill_quantities={"missing": 10, "trade-1": 20},
        broker_orders=(), broker_trades=(_trade(quantity=25),),
        snapshot_complete=True,
    )
    assert {x.status for x in report.issues} == {
        ReconciliationStatus.LOCAL_FILL_MISSING_AT_BROKER,
        ReconciliationStatus.FILL_QUANTITY_MISMATCH,
    }


def test_duplicate_broker_order_and_trade_ids_are_reported():
    report = reconcile_snapshot(
        account_id="acct-1", broker="angel_one", expected_orders=_expected(),
        local_fill_quantities={"trade-1": 25},
        broker_orders=(_order(), _order()),
        broker_trades=(_trade(), _trade()), snapshot_complete=True,
    )
    assert {x.status for x in report.issues} == {
        ReconciliationStatus.DUPLICATE_BROKER_ORDER_ID,
        ReconciliationStatus.DUPLICATE_BROKER_TRADE_ID,
    }


@pytest.mark.parametrize("kwargs", [
    {"account_id": "", "broker": "angel_one"},
    {"account_id": "acct-1", "broker": ""},
])
def test_account_and_broker_scope_are_required(kwargs):
    with pytest.raises(ValueError):
        reconcile_snapshot(
            **kwargs, expected_orders=(), local_fill_quantities={},
            broker_orders=(), broker_trades=(), snapshot_complete=True,
        )
