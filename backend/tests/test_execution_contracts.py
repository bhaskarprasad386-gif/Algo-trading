import pytest

from app.execution.contracts import (
    ExecutionIntent,
    ExecutionLeg,
    ExecutionMode,
    IntentStatus,
    LegProgress,
    OrderSide,
    validate_basket_legs,
)


def _intent():
    return ExecutionIntent(
        intent_id="intent-1",
        account_id="paper-account-1",
        idempotency_key="request-1",
        correlation_id="corr-1",
        mode=ExecutionMode.PAPER,
        created_at_ns=100,
    )


def test_intent_transition_is_immutable_and_follows_validated_path():
    original = _intent()
    validating = original.transition(IntentStatus.VALIDATING)
    risk = validating.transition(IntentStatus.RISK_CHECK)

    assert original.status is IntentStatus.CREATED
    assert validating.status is IntentStatus.VALIDATING
    assert risk.status is IntentStatus.RISK_CHECK
    assert risk.account_id == original.account_id


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (IntentStatus.CREATED, IntentStatus.SUBMITTING),
        (IntentStatus.SUBMITTING, IntentStatus.FILLED),
        (IntentStatus.UNKNOWN, IntentStatus.WORKING),
        (IntentStatus.COMPLETED, IntentStatus.SUBMITTING),
        (IntentStatus.REJECTED, IntentStatus.APPROVED),
    ],
)
def test_intent_rejects_skipped_or_unsafe_transitions(start, target):
    intent = _intent().transition(IntentStatus.VALIDATING) if start is IntentStatus.CREATED else _intent()
    if start is not IntentStatus.CREATED:
        intent = ExecutionIntent(
            intent_id="i", account_id="a", idempotency_key="k",
            correlation_id="c", mode=ExecutionMode.PAPER, status=start,
        )
    with pytest.raises(ValueError, match="invalid intent transition"):
        intent.transition(target)


def test_unknown_submission_must_enter_reconciliation_before_resuming():
    unknown = ExecutionIntent(
        intent_id="i", account_id="a", idempotency_key="k",
        correlation_id="c", mode=ExecutionMode.LIVE, status=IntentStatus.UNKNOWN,
    )
    reconciling = unknown.transition(IntentStatus.RECONCILIATION_REQUIRED)
    resumed = reconciling.transition(IntentStatus.WORKING)

    assert resumed.status is IntentStatus.WORKING


def test_basket_requires_unique_legs_and_equal_quantities():
    legs = (
        ExecutionLeg("ce-buy", "NFO:NIFTY-CE", OrderSide.BUY, 50, limit_price=100.0),
        ExecutionLeg("ce-sell", "NFO:NIFTY-CE-HIGH", OrderSide.SELL, 50, limit_price=80.0),
        ExecutionLeg("pe-buy", "NFO:NIFTY-PE", OrderSide.BUY, 50, limit_price=90.0),
        ExecutionLeg("pe-sell", "NFO:NIFTY-PE-HIGH", OrderSide.SELL, 50, limit_price=70.0),
    )
    validate_basket_legs(legs)


def test_basket_rejects_duplicate_leg_ids_and_unequal_quantity():
    one = ExecutionLeg("same", "NFO:A", OrderSide.BUY, 50, limit_price=10.0)
    duplicate = ExecutionLeg("same", "NFO:B", OrderSide.SELL, 50, limit_price=11.0)
    with pytest.raises(ValueError, match="unique"):
        validate_basket_legs((one, duplicate))

    other_qty = ExecutionLeg("other", "NFO:B", OrderSide.SELL, 25, limit_price=11.0)
    with pytest.raises(ValueError, match="equal quantities"):
        validate_basket_legs((one, other_qty))


def test_leg_progress_rejects_filled_status_without_full_quantity():
    with pytest.raises(ValueError, match="FILLED requires"):
        LegProgress("leg-1", requested_quantity=50, filled_quantity=25, status="FILLED")


def test_market_and_limit_order_price_contracts_are_explicit():
    with pytest.raises(ValueError, match="positive limit_price"):
        ExecutionLeg("leg-1", "NFO:A", OrderSide.BUY, 50)
    with pytest.raises(ValueError, match="cannot carry"):
        ExecutionLeg("leg-2", "NFO:A", OrderSide.BUY, 50, order_type="MARKET", limit_price=10.0)
