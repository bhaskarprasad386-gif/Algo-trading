from types import SimpleNamespace

from app.execution.broker_reconciliation import (
    BrokerOrderSnapshot,
    BrokerSnapshot,
    BrokerTradeSnapshot,
    ReconciliationStatus,
)
from app.execution.recovery_review import (
    RecoveryDisposition,
    LocalBrokerOrder,
    review_recovery_candidate,
)
from app.execution.contracts import OrderSide


def _candidate():
    legs = (
        SimpleNamespace(leg_id="cb", instrument="NIFTY-CE", side=OrderSide.BUY, quantity=50),
        SimpleNamespace(leg_id="cs", instrument="NIFTY-CE-HIGH", side=OrderSide.SELL, quantity=50),
    )
    persisted = SimpleNamespace(
        intent=SimpleNamespace(intent_id="intent-1", account_id="acct-1"),
        legs=legs,
    )
    return SimpleNamespace(persisted=persisted)


def _snapshot(*, complete=True, orders=None, trades=None):
    return BrokerSnapshot(
        account_id="acct-1",
        broker="zerodha",
        orders=tuple(orders or ()),
        trades=tuple(trades or ()),
        complete=complete,
    )


def _local_orders():
    return (
        LocalBrokerOrder("cb", "ord-1", "NIFTY-CE", "BUY", 50),
        LocalBrokerOrder("cs", "ord-2", "NIFTY-CE-HIGH", "SELL", 50),
    )


def test_matching_intent_is_consistent_but_never_authorizes_resume():
    review = review_recovery_candidate(
        candidate=_candidate(),
        broker="zerodha",
        local_orders=_local_orders(),
        local_fill_quantities={"trade-1": 25},
        snapshot=_snapshot(
            orders=(
                BrokerOrderSnapshot("ord-1", "NIFTY-CE", "BUY", 50, 25),
                BrokerOrderSnapshot("ord-2", "NIFTY-CE-HIGH", "SELL", 50, 0),
            ),
            trades=(BrokerTradeSnapshot("trade-1", "ord-1", 25),),
        ),
    )
    assert review.disposition is RecoveryDisposition.CONSISTENT_READ_ONLY
    assert review.is_consistent is True
    assert review.matched_order_count == 2


def test_untracked_leg_with_similar_broker_order_is_blocked_for_manual_review():
    review = review_recovery_candidate(
        candidate=_candidate(),
        broker="zerodha",
        local_orders=(LocalBrokerOrder("cb", "ord-1", "NIFTY-CE", "BUY", 50),),
        local_fill_quantities={},
        snapshot=_snapshot(orders=(
            BrokerOrderSnapshot("ord-1", "NIFTY-CE", "BUY", 50, 0),
            BrokerOrderSnapshot("ord-2", "NIFTY-CE-HIGH", "SELL", 50, 0),
        )),
    )
    assert review.disposition is RecoveryDisposition.BLOCKED
    assert ReconciliationStatus.MISSING_BROKER_ORDER in {x.status for x in review.issues}
    assert any("manual identity verification" in x.detail for x in review.issues)


def test_unrelated_account_orders_and_trades_are_not_misattributed():
    review = review_recovery_candidate(
        candidate=_candidate(),
        broker="zerodha",
        local_orders=_local_orders(),
        local_fill_quantities={},
        snapshot=_snapshot(orders=(
            BrokerOrderSnapshot("ord-1", "NIFTY-CE", "BUY", 50, 0),
            BrokerOrderSnapshot("ord-2", "NIFTY-CE-HIGH", "SELL", 50, 0),
            BrokerOrderSnapshot("other", "BANKNIFTY-CE", "BUY", 15, 0),
        ), trades=(
            BrokerTradeSnapshot("unrelated-trade", "other", 15),
        )),
    )
    assert review.disposition is RecoveryDisposition.CONSISTENT_READ_ONLY
    assert review.issues == ()


def test_incomplete_snapshot_blocks_recovery_review():
    review = review_recovery_candidate(
        candidate=_candidate(),
        broker="zerodha",
        local_orders=_local_orders(),
        local_fill_quantities={},
        snapshot=_snapshot(complete=False, orders=(
            BrokerOrderSnapshot("ord-1", "NIFTY-CE", "BUY", 50, 0),
            BrokerOrderSnapshot("ord-2", "NIFTY-CE-HIGH", "SELL", 50, 0),
        )),
    )
    assert review.disposition is RecoveryDisposition.BLOCKED
    assert ReconciliationStatus.INCOMPLETE_SNAPSHOT in {x.status for x in review.issues}


def test_cross_account_or_broker_snapshot_is_rejected():
    wrong = BrokerSnapshot("acct-2", "zerodha", (), (), True)
    try:
        review_recovery_candidate(
            candidate=_candidate(), broker="zerodha",
            local_orders=(), local_fill_quantities={}, snapshot=wrong,
        )
    except ValueError as exc:
        assert "scope" in str(exc)
    else:
        raise AssertionError("cross-account snapshot must be rejected")
