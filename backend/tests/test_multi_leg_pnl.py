from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.multi_leg_pnl import build_basket_pnl


def test_two_leg_basket_has_signed_gross_net_pnl_and_charges():
    entries = {
        "basket:call": SimFill("basket:call", "OPT-CE", ExecutionSide.BUY, 1, 10.0, 100),
        "basket:put": SimFill("basket:put", "OPT-PE", ExecutionSide.SELL, 1, 8.0, 100),
    }
    exits = {
        "basket:call": SimFill("basket:call", "OPT-CE", ExecutionSide.BUY, 1, 12.0, 200),
        "basket:put": SimFill("basket:put", "OPT-PE", ExecutionSide.SELL, 1, 6.0, 200),
    }
    result = build_basket_pnl("basket", entries, exits, charges_rate=0.01)

    assert result.gross_pnl == 4.0
    assert result.charges == 0.36
    assert result.net_pnl == 3.64


def test_basket_rejects_partial_entry_exit_sets():
    entry = SimFill("basket:a", "A", ExecutionSide.BUY, 1, 10.0, 1)
    exit_ = SimFill("basket:b", "B", ExecutionSide.SELL, 1, 10.0, 2)
    try:
        build_basket_pnl("basket", {"basket:a": entry}, {"basket:b": exit_})
        raise AssertionError("expected incomplete basket rejection")
    except ValueError as exc:
        assert "same legs" in str(exc)
