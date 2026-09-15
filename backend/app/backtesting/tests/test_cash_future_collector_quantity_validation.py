import pytest

from app.scanner.cash_future_collector import _full_quote


def _quote(trade_volume=10, open_interest=20):
    return {
        "status": True,
        "data": {
            "fetched": [{
                "ltp": 100.0,
                "tradeVolume": trade_volume,
                "opnInterest": open_interest,
            }]
        },
    }


def test_full_quote_rejects_fractional_trade_volume():
    with pytest.raises(ValueError, match="tradeVolume must be a non-negative integer"):
        _full_quote(_quote(trade_volume=10.5))


def test_full_quote_rejects_fractional_open_interest():
    with pytest.raises(ValueError, match="opnInterest must be a non-negative integer"):
        _full_quote(_quote(open_interest=20.25))


def test_full_quote_accepts_integer_like_numeric_quantities():
    quote = _full_quote(_quote(trade_volume=10.0, open_interest=20.0))
    assert quote["volume"] == 10
    assert quote["oi"] == 20
