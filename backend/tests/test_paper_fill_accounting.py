from app.execution.paper_fill_accounting import account_paper_fill


def test_paper_fill_adapter_uses_confirmed_buy_and_sell_fills():
    state, delta = account_paper_fill(
        side="BUY",
        price=100.0,
        quantity=10,
        current_quantity=0,
        current_average_price=0,
    )
    assert state.quantity == 10.0
    assert state.average_price == 100.0
    assert delta == 0.0

    state, delta = account_paper_fill(
        side="SELL",
        price=110.0,
        quantity=4,
        current_quantity=state.quantity,
        current_average_price=state.average_price,
        current_realized_pnl=state.realized_pnl,
    )
    assert state.quantity == 6.0
    assert state.average_price == 100.0
    assert delta == 40.0


def test_paper_fill_adapter_supports_short_reversal():
    state, delta = account_paper_fill(
        side="SELL",
        price=120.0,
        quantity=5,
        current_quantity=0,
        current_average_price=0,
    )
    assert state.quantity == -5.0
    assert state.average_price == 120.0
    assert delta == 0.0

    state, delta = account_paper_fill(
        side="BUY",
        price=110.0,
        quantity=7,
        current_quantity=state.quantity,
        current_average_price=state.average_price,
        current_realized_pnl=state.realized_pnl,
    )
    assert state.quantity == 2.0
    assert state.average_price == 110.0
    assert delta == 50.0
