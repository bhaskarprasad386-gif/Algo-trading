import pytest

from app.backtesting.high_resolution import EventDataSpec, event_identity, validate_event_payload


def test_event_payload_accepts_arbitrary_provider_fields_and_nested_values():
    payload = {
        "ltp": 25123.45,
        "bid": {"price": 25123.40, "quantity": 1200},
        "ask": {"price": 25123.50, "quantity": 800},
        "depth": [{"price": 25123.40, "quantity": 600}],
        "open_interest": 123456,
        "provider_meta": {"venue": "NFO", "flags": ["trade", "depth"]},
    }

    assert validate_event_payload(payload) is None


def test_event_spec_and_identity_preserve_same_timestamp_sequence_order():
    spec = EventDataSpec(timeframe="order_book", timestamp_unit="ns", ordered_by_sequence=True)

    assert spec.timeframe == "order_book"
    assert event_identity(timestamp_ns=1_000, sequence=2) == (1_000, 2)
    assert event_identity(timestamp_ns=1_000, sequence=1) == (1_000, 1)


def test_event_payload_rejects_non_mapping_without_restricting_schema():
    with pytest.raises(TypeError, match="event payload must be a mapping"):
        validate_event_payload([("ltp", 100)])
