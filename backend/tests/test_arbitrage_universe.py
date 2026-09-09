import pytest

from app.backtesting.arbitrage_universe import (
    ArbitrageContract,
    ArbitrageUniversePolicy,
    ordered_strike_positions,
)


def _chain(count_below=15, count_above=15):
    return [
        ArbitrageContract("NSE", "EQUITY_FNO", "ABC", 20260924, strike=100 - i)
        for i in range(1, count_below + 1)
    ] + [
        ArbitrageContract("NSE", "EQUITY_FNO", "ABC", 20260924, strike=100 + i)
        for i in range(1, count_above + 1)
    ]


def test_positions_are_actual_chain_positions_not_rupee_gaps():
    chain = [
        ArbitrageContract("NSE", "EQUITY_FNO", "ABC", 20260924, strike=s)
        for s in (80, 90, 97, 99, 101, 105, 120)
    ]
    positions = ordered_strike_positions(chain, atm=100)
    assert [(p.strike, p.position, p.side) for p in positions] == [
        (99, 1, "BELOW_ATM"), (97, 2, "BELOW_ATM"), (90, 3, "BELOW_ATM"), (80, 4, "BELOW_ATM"),
        (101, 1, "ABOVE_ATM"), (105, 2, "ABOVE_ATM"), (120, 3, "ABOVE_ATM"),
    ]


def test_box_stock_requires_exactly_five_each_side():
    selected = ArbitrageUniversePolicy.box_stock_strikes(_chain(), atm=100)
    assert selected == (99, 98, 97, 96, 95, 101, 102, 103, 104, 105)


def test_box_index_selects_positions_three_through_fifteen_both_sides():
    selected = ArbitrageUniversePolicy.box_index_strikes(_chain(), atm=100)
    assert selected == tuple(range(97, 84, -1)) + tuple(range(103, 116))


def test_box_rejects_missing_historical_chain_positions():
    with pytest.raises(ValueError, match="exactly 5"):
        ArbitrageUniversePolicy.box_stock_strikes(_chain(count_below=4), atm=100)


def test_synthetic_stock_and_index_limits_use_actual_positions():
    chain = _chain()
    assert ArbitrageUniversePolicy.synthetic_stock_strikes(chain, atm=100) == (
        99, 98, 97, 96, 95, 101, 102, 103, 104, 105
    )
    assert ArbitrageUniversePolicy.synthetic_index_strikes(chain, atm=100) == (
        tuple(range(99, 84, -1)) + tuple(range(101, 116))
    )


def test_calendar_includes_supported_nse_bse_and_commodity_and_excludes_unsupported():
    contracts = [
        ArbitrageContract("NSE", "EQUITY_FNO", "ABC", 20260924),
        ArbitrageContract("BSE", "INDEX_FNO", "SENSEX", 20260924),
        ArbitrageContract("MCX", "COMMODITY", "GOLD", 20261005),
        ArbitrageContract("NSE", "EQUITY_FNO", "NOPE", 20260924, supported=False),
    ]
    selected = ArbitrageUniversePolicy.calendar(contracts)
    assert {c.symbol for c in selected} == {"ABC", "SENSEX", "GOLD"}


def test_liquidity_uses_real_catalog_fields():
    contracts = [
        ArbitrageContract("NSE", "EQUITY_FNO", "LIQ", 20260924, strike=100,
                           volume=1000, oi=5000, bid=10, ask=10.2),
        ArbitrageContract("NSE", "EQUITY_FNO", "WIDE", 20260924, strike=101,
                           volume=1000, oi=5000, bid=10, ask=12),
        ArbitrageContract("NSE", "EQUITY_FNO", "LOWVOL", 20260924, strike=102,
                           volume=10, oi=5000, bid=10, ask=10.1),
    ]
    selected = ArbitrageUniversePolicy.liquid(contracts, min_volume=100, min_oi=1000, max_spread_pct=5)
    assert [c.symbol for c in selected] == ["LIQ"]
