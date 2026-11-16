from dataclasses import replace

import pytest

from app.execution.broker_events import (
    BrokerOrderEvent,
    BrokerOrderEventStatus,
    assert_same_event_payload,
    broker_event_identity,
    broker_fill_identity,
    validate_event_scope,
)


def make_event(**overrides):
    values = {
        "broker": "fyers",
        "account_id": "acct-1",
        "event_id": "evt-1",
        "broker_order_id": "order-1",
        "status": BrokerOrderEventStatus.PARTIALLY_FILLED,
        "cumulative_filled_quantity": 25,
        "fill_id": "trade-1",
        "fill_quantity": 25,
        "average_fill_price": 101.5,
        "occurred_at_ns": 10,
    }
    values.update(overrides)
    return BrokerOrderEvent(**values)


@pytest.mark.parametrize(
    "status",
    [
        BrokerOrderEventStatus.NEW,
        BrokerOrderEventStatus.OPEN,
        BrokerOrderEventStatus.PARTIALLY_FILLED,
        BrokerOrderEventStatus.FILLED,
        BrokerOrderEventStatus.CANCEL_PENDING,
        BrokerOrderEventStatus.CANCELLED,
        BrokerOrderEventStatus.REJECTED,
        BrokerOrderEventStatus.UNKNOWN,
    ],
)
def test_normalized_order_event_accepts_all_lifecycle_states(status):
    filled = 1 if status in {
        BrokerOrderEventStatus.PARTIALLY_FILLED,
        BrokerOrderEventStatus.FILLED,
    } else 0
    event = make_event(status=status, cumulative_filled_quantity=filled)
    assert event.status is status


def test_event_and_fill_identity_are_account_scoped():
    event = make_event()
    assert broker_event_identity(event) == ("fyers", "acct-1", "evt-1")
    assert broker_fill_identity(event) == ("fyers", "acct-1", "trade-1")


def test_exact_duplicate_callback_is_idempotent():
    event = make_event()
    assert_same_event_payload(event, replace(event))


def test_same_callback_identity_with_changed_payload_is_rejected():
    with pytest.raises(ValueError, match="changed payload"):
        assert_same_event_payload(
            make_event(), make_event(cumulative_filled_quantity=30, fill_quantity=30)
        )


def test_event_from_another_account_is_rejected():
    with pytest.raises(ValueError, match="account scope"):
        validate_event_scope(make_event(account_id="acct-2"), broker="fyers", account_id="acct-1")


@pytest.mark.parametrize(
    "overrides",
    [
        {"cumulative_filled_quantity": -1},
        {"fill_id": "trade-1", "fill_quantity": None},
        {"fill_id": None, "fill_quantity": 1},
        {"status": BrokerOrderEventStatus.PARTIALLY_FILLED, "cumulative_filled_quantity": 0},
        {"status": BrokerOrderEventStatus.FILLED, "cumulative_filled_quantity": 0},
    ],
)
def test_invalid_fill_event_is_rejected(overrides):
    with pytest.raises(ValueError):
        make_event(**overrides)
