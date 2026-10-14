from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner


def test_cash_future_depth_diagnostic_compares_shape_without_retaining_raw_values():
    key = InstrumentKey("NSE", "NSE", "123")
    runner = LiveCashFutureCommonRunner(data_db=":memory:", manager=object())
    runner._metadata = {
        key: {"leg": "CASH", "underlying": "ABC", "contract_month": "CASH"}
    }

    runner._on_raw_tick({
        "exchange_type": 1,
        "token": "123",
        "last_traded_price": 12345,
        "best_5_buy_data": [{"price": 12340, "quantity": 10, "orders": 2}],
        "best_5_sell_data": [{"price": 12350, "quantity": 12, "orders": 3}],
    })
    record = MarketDataRecord(
        instrument=key,
        symbol="ABC-EQ",
        instrument_type=InstrumentType.EQUITY,
        timestamp_ns=1,
        ltp=123.45,
        bid=123.40,
        ask=123.50,
        bid_qty=10,
        ask_qty=12,
    )
    runner._update_depth_diagnostic_record(record)

    snapshot = runner.snapshot()["depth_diagnostic"]
    assert snapshot["sampled_instruments"] == 1
    sample = snapshot["samples"][0]
    assert sample["raw_depth_shapes"]["best_5_buy_data"]["type"] == "list"
    assert sample["raw_depth_shapes"]["best_5_buy_data"]["first_item_keys"] == [
        "orders", "price", "quantity"
    ]
    assert sample["normalized"]["bid"] == 123.40
    assert sample["normalized"]["ask"] == 123.50
    assert "12340" not in repr(snapshot)
    assert "12350" not in repr(snapshot)
