from types import SimpleNamespace

from app.scanner.iv_skew_flip.early_move import IVEarlyMoveDetector


def test_early_move_detector_fails_closed_without_fresh_new_ticks():
    detector = IVEarlyMoveDetector()
    result = detector.update(
        quotes=(), metrics=(), spot_price=24000.0, now_ns=100_000_000_000,
    )
    assert result["state"] == "WATCH"
    assert result["signals_allowed"] is False


def test_early_move_detector_does_not_double_count_repeated_volume_ticks():
    detector = IVEarlyMoveDetector()
    quote = SimpleNamespace(
        token="ce", timestamp_ns=100_000_000_000, volume=100,
        option_type="CE", mark=20.0,
    )
    metric = {"token": "ce", "iv": 18.0}
    detector.update(quotes=(quote,), metrics=(metric,), spot_price=24000, now_ns=100_000_000_000)
    first = detector.update(quotes=(quote,), metrics=(metric,), spot_price=24000, now_ns=101_000_000_000)
    assert first["signals_allowed"] is False
    assert first["volume_ratio"] is None


def test_early_move_detector_requires_volume_iv_premium_and_spot_confluence():
    detector = IVEarlyMoveDetector()
    base = 100_000_000_000
    # Warm the rolling buckets with modest cumulative volume.
    for i in range(1, 25):
        ts = base + i * 10_000_000_000
        quotes = (
            SimpleNamespace(token="ce1", timestamp_ns=ts, volume=100 + i * 10, option_type="CE", mark=20.0 + i * .01),
            SimpleNamespace(token="ce2", timestamp_ns=ts, volume=200 + i * 10, option_type="CE", mark=25.0 + i * .01),
        )
        metrics = (
            {"token": "ce1", "iv": 18.0 + i * .01},
            {"token": "ce2", "iv": 19.0 + i * .01},
        )
        detector.update(quotes=quotes, metrics=metrics, spot_price=24000 + i * .01, now_ns=ts)
    # A sharp new burst across two contracts.
    ts = base + 250_000_000_000
    quotes = (
        SimpleNamespace(token="ce1", timestamp_ns=ts, volume=1000, option_type="CE", mark=22.0),
        SimpleNamespace(token="ce2", timestamp_ns=ts, volume=1100, option_type="CE", mark=27.0),
    )
    metrics = ({"token": "ce1", "iv": 20.0}, {"token": "ce2", "iv": 21.0})
    result = detector.update(quotes=quotes, metrics=metrics, spot_price=24010, now_ns=ts)
    assert result["direction"] == "BULLISH"
    assert result["volume_ratio"] >= 4
    assert result["signals_allowed"] is True
    assert result["state"] in {"PRE_MOVE", "CONFIRMED"}
