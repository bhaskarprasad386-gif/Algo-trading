import pytest

from app.backtesting.historical_expected_identities import missing_expected_identities


def test_same_timestamp_events_are_checked_independently_by_sequence():
    expected = ((1_000, 0), (1_000, 1), (1_000, 2), (2_000, None))
    observed = ((1_000, 0), (1_000, 2), (2_000, None))

    assert missing_expected_identities(expected, observed) == ((1_000, 1),)


def test_irregular_event_identity_timing_does_not_invent_missing_events():
    expected = ((1_000, None), (1_001, None), (9_999, None))
    observed = ((1_000, None), (9_999, None))

    assert missing_expected_identities(expected, observed) == ((1_001, None),)


def test_expected_identity_duplicates_are_deduplicated():
    assert missing_expected_identities(
        ((1_000, 0), (1_000, 0), (2_000, None)),
        ((2_000, None),),
    ) == ((1_000, 0),)


@pytest.mark.parametrize("identity", [(-1, None), (1_000, -1)])
def test_negative_event_identity_is_rejected(identity):
    with pytest.raises(ValueError, match="cannot be negative"):
        missing_expected_identities((identity,), ())
