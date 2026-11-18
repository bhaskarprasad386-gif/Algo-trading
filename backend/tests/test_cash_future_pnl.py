from app.backtesting.cash_future_pnl import CashFutureTrade, cash_future_gap


def test_cash_future_pnl_is_combined_from_two_legs():
    trade = CashFutureTrade(
        spot_entry=100,
        spot_exit=105,
        future_entry=102,
        future_exit=96,
        quantity=2,
        lot_size=10,
        brokerage=5,
        funding=3,
        slippage=2,
    )
    assert trade.units == 20
    assert trade.spot_pnl == 100
    assert trade.future_pnl == 120
    assert trade.gross_pnl == 220
    assert trade.net_pnl == 210


def test_gap_is_future_minus_spot():
    assert cash_future_gap(100, 103.5) == 3.5
