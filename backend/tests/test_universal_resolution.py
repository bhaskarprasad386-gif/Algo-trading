import pytest

from app.backtesting.universal import MarketEvent, validate_source_resolution


def test_source_resolution_accepts_exact_requested_spacing():
    events = [
        MarketEvent(1_000_000, "NIFTY"),
        MarketEvent(2_000_000, "NIFTY"),
    ]
    validate_source_resolution(events, 1_000_000)


def test_source_resolution_rejects_data_that_does_not_prove_requested_spacing():
    events = [
        MarketEvent(1_000_000, "NIFTY"),
        MarketEvent(3_000_000, "NIFTY"),
    ]
    with pytest.raises(ValueError, match="does not prove"):
        validate_source_resolution(events, 1_000_000)


def test_source_resolution_rejects_invalid_resolution_type():
    with pytest.raises(TypeError, match="must be an integer"):
        validate_source_resolution([], 1.0)
