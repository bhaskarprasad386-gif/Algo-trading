from app.scanner.rsi_obv import obv, rsi, scan


def make_candles(closes, volumes=None):
    volumes = volumes or [100] * len(closes)
    return [["2026-01-01", p, p, p, p, v] for p, v in zip(closes, volumes)]


def test_rsi_returns_value_with_enough_data():
    closes = [100 + i for i in range(20)]
    assert rsi(closes) == 100.0


def test_obv_rises_when_closes_rise():
    assert obv([100, 101, 102], [0, 10, 20]) == 30.0


def test_scanner_rejects_insufficient_candles():
    assert scan(make_candles([100] * 10))["reason"] == "insufficient_candles"


def test_scanner_result_has_contract_fields():
    result = scan(make_candles([100 + (i % 2) for i in range(25)]))
    assert "qualified" in result
    assert "rsi" in result
    assert "obv" in result
    assert "rsi_rising" in result
    assert "obv_rising" in result
