from app.market_data.candle_normalizer import normalize_candles


def test_normalize_candle_array():
    rows = [["2026-09-01 09:15", "100", "105", "99", "103", "1200"]]
    result = normalize_candles(rows)
    assert result == [{
        "timestamp": "2026-09-01 09:15",
        "open": "100",
        "high": "105",
        "low": "99",
        "close": "103",
        "volume": "1200",
    }]


def test_normalize_skips_invalid_rows():
    assert normalize_candles([["bad"]]) == []
