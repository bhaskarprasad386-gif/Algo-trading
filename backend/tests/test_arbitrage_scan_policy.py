from app.backtesting.arbitrage_scan_policy import (
    ScanPolicy,
    enumerate_box_pairs,
    enumerate_synthetic_strikes,
    strike_distance_from_atm,
)


STRIKES = (90, 95, 100, 105, 110, 115, 120, 125)


def test_stock_box_uses_atm_position_not_rupee_gap():
    pairs = enumerate_box_pairs(STRIKES, atm_strike=100, instrument_class="STOCK")
    distances = {distance for _, _, distance in pairs}
    assert distances == {3, 4, 5}
    assert (100.0, 115.0, 3) in pairs
    assert (100.0, 120.0, 4) in pairs


def test_index_box_supports_three_through_fifteen_positions_when_chain_has_them():
    strikes = tuple(range(75, 226, 5))
    pairs = enumerate_box_pairs(strikes, atm_strike=150, instrument_class="INDEX")
    distances = {distance for _, _, distance in pairs}
    assert distances == set(range(3, 16))


def test_synthetic_stock_is_both_sides_of_atm_with_max_seven_positions():
    strikes = tuple(range(65, 136, 5))
    result = enumerate_synthetic_strikes(strikes, atm_strike=100, instrument_class="STOCK")
    assert result[0] == (65.0, 7, "LOWER")
    assert (100.0, 0, "ATM") in result
    assert (120.0, 4, "UPPER") in result
    assert (135.0, 7, "UPPER") in result
    assert max(distance for _, distance, _ in result) == 7


def test_synthetic_index_allows_ten_positions_when_chain_has_them():
    strikes = tuple(range(100, 201))
    result = enumerate_synthetic_strikes(strikes, atm_strike=150, instrument_class="INDEX")
    assert max(distance for _, distance, _ in result) == 10
    assert (140.0, 10, "LOWER") in result
    assert (160.0, 10, "UPPER") in result
    assert 139.0 not in {strike for strike, _, _ in result}


def test_distance_is_chain_position_count():
    strikes = (100, 150, 250, 400)
    assert strike_distance_from_atm(strikes, atm_strike=150, strike=400) == 2


def test_custom_policy_can_tighten_limits_without_changing_engine():
    policy = ScanPolicy(stock_box_distances=(3,), stock_synthetic_radius=2)
    pairs = enumerate_box_pairs(STRIKES, atm_strike=100, instrument_class="STOCK", policy=policy)
    assert pairs == ((100.0, 115.0, 3),)
    assert all(
        distance <= 2
        for _, distance, _ in enumerate_synthetic_strikes(
            STRIKES, atm_strike=100, instrument_class="STOCK", policy=policy
        )
    )


def test_synthetic_index_and_commodity_radii_are_ten():
    strikes = tuple(range(50, 151))
    assert max(d for _, d, _ in enumerate_synthetic_strikes(strikes, atm_strike=100, instrument_class="INDEX")) == 10
    assert max(d for _, d, _ in enumerate_synthetic_strikes(strikes, atm_strike=100, instrument_class="COMMODITY")) == 10
