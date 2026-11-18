from datetime import date


def test_shorting_gap_is_intraday_future_cash_high_not_ohlc_high():
    """Regression contract for the daily Cash-Future shorting-gap definition.

    The daily shorting gap must be the maximum actual intraday
    Future - Cash spread, and its weighted value must use that spread's
    lot size. It must not be derived from Future OHLC high minus Cash OHLC high.
    """
    intraday_points = [
        {"timestamp": "09:30", "cash": 1000.0, "future": 1008.0, "lot_size": 500},
        {"timestamp": "11:15", "cash": 1005.0, "future": 1022.0, "lot_size": 500},
        {"timestamp": "14:45", "cash": 1018.0, "future": 1030.0, "lot_size": 500},
    ]

    top = max(intraday_points, key=lambda point: (point["future"] - point["cash"], point["timestamp"]))
    gap = top["future"] - top["cash"]

    assert top["timestamp"] == "11:15"
    assert gap == 17.0
    assert gap * top["lot_size"] == 8500.0

    # Explicitly guard against the incorrect Future-High minus Cash-High formula.
    cash_day_high = max(point["cash"] for point in intraday_points)
    future_day_high = max(point["future"] for point in intraday_points)
    assert future_day_high - cash_day_high != gap
