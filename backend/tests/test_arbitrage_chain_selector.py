from backend.app.backtesting.arbitrage_chain_selector import (
    ChainContract,
    pair_by_strike,
    select_box_index,
    select_box_stock,
    select_calendar_expiries,
    select_synthetic_index,
    select_synthetic_stock,
)


def chain(instrument_class="STOCK", count=41):
    return [
        ChainContract(
            timestamp_ns=1,
            venue="NSE",
            underlying="TEST",
            instrument_class=instrument_class,
            expiry=20261001,
            strike=float(i),
            option_type="CE",
            lot_size=50,
            volume=10000,
            oi=20000,
            bid=10,
            ask=10.1,
        )
        for i in range(count)
    ]


def test_stock_box_uses_five_actual_positions_each_side():
    selected = select_box_stock(chain(), atm=20)
    assert [c.strike for c in selected] == list(range(15, 26))


def test_synthetic_stock_uses_five_actual_positions_each_side():
    selected = select_synthetic_stock(chain(), atm=20)
    assert [c.strike for c in selected] == list(range(15, 26))


def test_index_box_uses_positions_three_through_fifteen():
    selected = select_box_index(chain("INDEX"), atm=20)
    assert [c.strike for c in selected] == list(range(5, 18)) + list(range(23, 36))


def test_synthetic_index_uses_fifteen_actual_positions_each_side():
    selected = select_synthetic_index(chain("INDEX"), atm=20)
    assert [c.strike for c in selected] == list(range(5, 36))


def test_missing_strikes_are_not_fabricated():
    contracts = chain()
    contracts = [c for c in contracts if c.strike not in {15, 19, 25}]
    selected = select_box_stock(contracts, atm=20)
    assert 15 not in [c.strike for c in selected]
    assert 19 not in [c.strike for c in selected]
    assert 25 not in [c.strike for c in selected]


def test_calendar_expiries_are_real_pairs_only():
    contracts = chain() + [
        ChainContract(1, "NSE", "TEST", "STOCK", 20261101, 20, "CE", 50, 100, 200, 10, 10.1),
        ChainContract(1, "NSE", "TEST", "STOCK", 20261201, 20, "CE", 50, 100, 200, 10, 10.1),
    ]
    assert select_calendar_expiries(contracts) == (
        (20261001, 20261101), (20261001, 20261201), (20261101, 20261201)
    )


def test_box_pairs_preserve_actual_strike_identity():
    pairs = pair_by_strike(chain(), expiry=20261001, option_type="CE")
    assert pairs[0].low.strike == 0
    assert pairs[0].high.strike == 1
    assert pairs[0].low_position == 0
    assert pairs[0].high_position == 1
