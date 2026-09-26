from app.market_data.live_cash_future_stream import LiveCashFutureOneSecondCollector


def test_live_quote_best_side_normalizes_snapquote_price():
    message = {"best_5_buy_data": [{"price": "10000", "quantity": "10"}],
               "best_5_sell_data": [{"price": "10020", "quantity": "12"}]}
    assert LiveCashFutureOneSecondCollector._best_side(message, "best_5_buy_data") == 100.0
    assert LiveCashFutureOneSecondCollector._best_side(message, "best_5_sell_data") == 100.2


def test_live_quote_best_side_rejects_missing_depth():
    assert LiveCashFutureOneSecondCollector._best_side({}, "best_5_buy_data") is None
