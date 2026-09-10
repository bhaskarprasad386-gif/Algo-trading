from app.backtesting.high_resolution import EventDataSpec, event_identity, is_event_timeframe, validate_event_payload


def test_event_timeframe_is_non_cadenced():
    assert is_event_timeframe("tick")
    assert is_event_timeframe("order_book")
    assert not is_event_timeframe("1m")


def test_event_identity_preserves_same_timestamp_sequence():
    assert event_identity(timestamp_ns=1_000, sequence=2) == (1_000, 2)


def test_event_spec_uses_nanosecond_storage_precision():
    spec = EventDataSpec()
    assert spec.timeframe == "tick"
    assert spec.timestamp_unit == "ns"
    assert spec.ordered_by_sequence


def test_event_payload_allows_provider_specific_fields():
    validate_event_payload({"price": 100.5, "bid": 100.4, "ask": 100.6})
