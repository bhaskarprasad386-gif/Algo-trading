from app.scanner.iv_skew_flip.equity_pressure import (
    ComponentSample,
    ComponentWeight,
    EquityComponentPressure,
)


def tracker(**kwargs):
    return EquityComponentPressure(
        [ComponentWeight("A", 50), ComponentWeight("B", 30), ComponentWeight("C", 20)],
        **kwargs,
    )


def test_weighted_pressure_tracks_direction_and_top_contributor():
    p = tracker(min_abs_return_pct=0.02, min_breadth_pct=60)
    base = 100_000_000_000
    for symbol, price in (("A", 100), ("B", 200), ("C", 300)):
        assert p.update(ComponentSample(symbol, base, price))
        assert p.update(ComponentSample(symbol, base + 5_000_000_000, price * 1.001))
        assert p.update(ComponentSample(symbol, base + 10_000_000_000, price * 1.002))
        assert p.update(ComponentSample(symbol, base + 20_000_000_000, price * 1.003))
        assert p.update(ComponentSample(symbol, base + 25_000_000_000, price * 1.0035))
        assert p.update(ComponentSample(symbol, base + 30_000_000_000, price * 1.004))
    result = p.snapshot(now_ns=base + 30_000_000_000)
    assert result["status"] == "LIVE"
    assert result["signals_allowed"] is True
    assert result["direction"] == "BULLISH"
    assert result["windows"]["5s"]["direction"] == "BULLISH"
    assert result["windows"]["30s"]["top_contributors"][0]["symbol"] == "A"


def test_incomplete_weight_coverage_fails_closed():
    p = tracker(min_weight_coverage=0.90)
    base = 100_000_000_000
    for symbol, price in (("A", 100), ("B", 200)):
        p.update(ComponentSample(symbol, base, price))
        p.update(ComponentSample(symbol, base + 30_000_000_000, price * 1.01))
    result = p.snapshot(now_ns=base + 30_000_000_000)
    assert result["status"] == "PARTIAL"
    assert result["signals_allowed"] is False
    assert result["direction"] == "WAITING"


def test_mixed_constituents_do_not_create_directional_pressure():
    p = tracker(min_abs_return_pct=0.01, min_breadth_pct=60)
    base = 100_000_000_000
    values = {"A": (100, 100.2), "B": (200, 199.6), "C": (300, 299.7)}
    for symbol, (start, end) in values.items():
        p.update(ComponentSample(symbol, base, start))
        p.update(ComponentSample(symbol, base + 5_000_000_000, end))
        p.update(ComponentSample(symbol, base + 10_000_000_000, end))
        p.update(ComponentSample(symbol, base + 20_000_000_000, end))
        p.update(ComponentSample(symbol, base + 25_000_000_000, end))
        p.update(ComponentSample(symbol, base + 30_000_000_000, end))
    result = p.snapshot(now_ns=base + 30_000_000_000)
    assert result["status"] == "LIVE"
    assert result["direction"] == "MIXED"
    assert result["signals_allowed"] is False
    assert result["timeframe_agreement"] is False


def test_rejects_out_of_order_unknown_and_invalid_prices():
    p = tracker()
    assert p.update(ComponentSample("A", 100, 100))
    assert not p.update(ComponentSample("A", 100, 101))
    assert not p.update(ComponentSample("UNKNOWN", 101, 100))
    assert not p.update(ComponentSample("B", 101, float("nan")))


def test_stale_components_are_excluded():
    p = tracker(max_tick_age_seconds=3)
    base = 100_000_000_000
    for symbol, price in (("A", 100), ("B", 200), ("C", 300)):
        p.update(ComponentSample(symbol, base, price))
        p.update(ComponentSample(symbol, base + 30_000_000_000, price * 1.01))
    result = p.snapshot(now_ns=base + 34_000_000_001)
    assert result["status"] == "STALE"
    assert result["signals_allowed"] is False


def test_rejects_non_finite_configuration_and_weight_total():
    import pytest

    for kwargs in (
        {"max_tick_age_seconds": float("nan")},
        {"min_weight_coverage": float("inf")},
        {"min_abs_return_pct": float("nan")},
        {"min_breadth_pct": float("inf")},
    ):
        with pytest.raises(ValueError):
            tracker(**kwargs)
    with pytest.raises(ValueError):
        EquityComponentPressure([
            ComponentWeight("A", 1e308),
            ComponentWeight("B", 1e308),
        ])
