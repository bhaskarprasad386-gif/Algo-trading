import pytest

from app.algo.strategy import RsiSidewaysScanner


def test_rsi_sideways_scanner_matches_valid_setup():
    closes = [100, 99.8, 100.1, 99.9, 100.2, 100.0, 100.3, 100.1, 100.4, 100.2, 100.5, 100.3, 100.6, 100.4, 100.7, 100.9, 101.1]
    volumes = [1000] * len(closes)

    result = RsiSidewaysScanner(sideways_window=10).scan(
        closes=closes,
        volumes=volumes,
        delivery_percent=55,
    )

    assert result["match"] is True
    assert result["checks"]["sideways"] is True
    assert result["checks"]["delivery_above_50"] is True


def test_rsi_sideways_scanner_rejects_low_delivery():
    closes = [100, 99.8, 100.1, 99.9, 100.2, 100.0, 100.3, 100.1, 100.4, 100.2, 100.5, 100.3, 100.6, 100.4, 100.7, 100.9, 101.1]
    volumes = [1000] * len(closes)

    result = RsiSidewaysScanner().scan(closes, volumes, 50)

    assert result["match"] is False
    assert result["checks"]["delivery_above_50"] is False


def test_rsi_sideways_scanner_rejects_mismatched_data():
    with pytest.raises(ValueError, match="same length"):
        RsiSidewaysScanner().scan([100, 101], [1000], 60)
