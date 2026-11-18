from app.backtesting.high_resolution import EventDataSpec, event_identity


def test_event_spec_rejects_non_ns_storage_unit():
    try:
        EventDataSpec(timestamp_unit="us")
    except ValueError:
        return
    raise AssertionError("expected nanosecond storage validation")


def test_event_identity_rejects_negative_sequence():
    try:
        event_identity(timestamp_ns=1, sequence=-1)
    except ValueError:
        return
    raise AssertionError("expected sequence validation")
