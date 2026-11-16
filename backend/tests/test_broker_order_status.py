"""Provider-neutral order status and cancel-race classification tests."""

import pytest

from app.brokers.order_status import (
    NormalizedOrderState,
    normalize_order_status,
)


@pytest.mark.parametrize("broker", ["angel_one", "zerodha", "fyers", "shoonya", "mstock"])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("open", NormalizedOrderState.OPEN),
        ("trigger pending", NormalizedOrderState.OPEN),
        ("partially_filled", NormalizedOrderState.PARTIALLY_FILLED),
        ("complete", NormalizedOrderState.FILLED),
        ("cancelled", NormalizedOrderState.CANCELLED),
        ("canceled by user", NormalizedOrderState.CANCELLED),
        ("rejected", NormalizedOrderState.REJECTED),
        ("some-new-vendor-code", NormalizedOrderState.UNKNOWN),
        (None, NormalizedOrderState.UNKNOWN),
        (2, NormalizedOrderState.UNKNOWN),
    ],
)
def test_normalizes_conservative_text_statuses_for_all_brokers(broker, raw, expected):
    evidence = normalize_order_status(
        broker=broker,
        raw_status=raw,
        quantity=50,
        filled_quantity=0,
    )
    assert evidence.state is expected
    assert evidence.remaining_quantity == 50
    assert evidence.broker == broker


def test_cancel_after_partial_fill_is_preserved_as_distinct_terminal_state():
    evidence = normalize_order_status(
        broker="zerodha",
        raw_status="CANCELLED",
        quantity=50,
        filled_quantity=20,
    )
    assert evidence.state is NormalizedOrderState.PARTIALLY_FILLED_CANCELLED
    assert evidence.terminal is True
    assert evidence.requires_reconciliation is True
    assert evidence.status_conflict is False
    assert evidence.filled_quantity == 20
    assert evidence.remaining_quantity == 30


def test_filled_quantity_wins_over_open_status_but_flags_conflict():
    evidence = normalize_order_status(
        broker="angel_one",
        raw_status="OPEN",
        quantity=50,
        filled_quantity=50,
    )
    assert evidence.state is NormalizedOrderState.FILLED
    assert evidence.terminal is True
    assert evidence.status_conflict is True
    assert evidence.requires_reconciliation is True


@pytest.mark.parametrize("raw", ["REJECTED", "UNKNOWN_VENDOR_STATUS", "PARTIALLY FILLED"])
def test_nonzero_fills_are_not_lost_when_terminal_or_unknown_status_arrives(raw):
    evidence = normalize_order_status(
        broker="fyers",
        raw_status=raw,
        quantity=50,
        filled_quantity=10,
    )
    assert evidence.state is NormalizedOrderState.PARTIALLY_FILLED
    assert evidence.filled_quantity == 10
    assert evidence.remaining_quantity == 40
    assert evidence.requires_reconciliation is True


def test_partial_status_without_fill_quantity_requires_reconciliation():
    evidence = normalize_order_status(
        broker="shoonya",
        raw_status="PARTIALLY FILLED",
        quantity=50,
        filled_quantity=0,
    )
    assert evidence.state is NormalizedOrderState.PARTIALLY_FILLED
    assert evidence.requires_reconciliation is True


def test_filled_status_without_fill_quantity_requires_reconciliation():
    evidence = normalize_order_status(
        broker="mstock",
        raw_status="COMPLETE",
        quantity=50,
        filled_quantity=0,
    )
    assert evidence.state is NormalizedOrderState.FILLED
    assert evidence.requires_reconciliation is True


@pytest.mark.parametrize(
    ("quantity", "filled"),
    [(0, 0), (-1, 0), (50, -1), (50, 51), (True, 0), (50, True), (50.0, 0)],
)
def test_invalid_order_quantities_fail_closed(quantity, filled):
    with pytest.raises(ValueError):
        normalize_order_status(
            broker="angel_one",
            raw_status="OPEN",
            quantity=quantity,
            filled_quantity=filled,
        )


@pytest.mark.parametrize("broker", ["", "unknown", "angelone", "angel-one"])
def test_unsupported_broker_fails_closed(broker):
    with pytest.raises(ValueError, match="unsupported broker"):
        normalize_order_status(
            broker=broker,
            raw_status="OPEN",
            quantity=1,
            filled_quantity=0,
        )
