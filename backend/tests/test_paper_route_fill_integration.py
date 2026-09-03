from app.execution.paper_routes import _accounting_after_fill


def test_route_sell_uses_executed_fill_for_realized_pnl():
    state, delta = _accounting_after_fill(
        side="BUY",
        price=100.0,
        quantity=10.0,
        current_quantity=0.0,
        current_average_price=0.0,
    )
    assert state.quantity == 10.0
    assert state.average_price == 100.0
    assert delta == 0.0

    state, delta = _accounting_after_fill(
        side="SELL",
        price=110.0,
        quantity=4.0,
        current_quantity=state.quantity,
        current_average_price=state.average_price,
        current_realized_pnl=state.realized_pnl,
    )
    assert state.quantity == 6.0
    assert state.average_price == 100.0
    assert delta == 40.0
    assert state.realized_pnl == 40.0


def test_route_exit_realizes_only_the_confirmed_exit_fill():
    state, delta = _accounting_after_fill(
        side="SELL",
        price=95.0,
        quantity=10.0,
        current_quantity=10.0,
        current_average_price=100.0,
        current_realized_pnl=0.0,
    )
    assert delta == -50.0
    assert state.quantity == 0.0
    assert state.average_price == 0.0
    assert state.realized_pnl == -50.0
