import pytest

from app.backtesting.cash_future_historical_download import _normalise_cash_instrument
from app.market_data.instruments import InstrumentMaster


def test_plain_cash_symbol_resolves_to_nse_token_symbol():
    master = InstrumentMaster()
    master.instruments = [
        {"symbol": "RELIANCE-EQ", "exch_seg": "NSE", "instrumenttype": "EQ", "token": "2885"},
    ]

    assert _normalise_cash_instrument(master, "RELIANCE-EQ", "NSE") == "NSE:2885:RELIANCE-EQ"


def test_colon_form_nse_token_is_preserved_without_master_lookup():
    master = InstrumentMaster()
    master.instruments = []

    assert _normalise_cash_instrument(master, "NSE:2885", "NSE") == "NSE:2885"


def test_colon_form_nse_token_symbol_is_preserved_without_master_lookup():
    master = InstrumentMaster()
    master.instruments = []

    assert _normalise_cash_instrument(master, "NSE:2885:RELIANCE-EQ", "NSE") == "NSE:2885:RELIANCE-EQ"


@pytest.mark.parametrize("value", ["", "NSE:", ":2885", "NSE::RELIANCE-EQ", "NSE:2885:", "NSE:2885:RELIANCE:EXTRA"])
def test_invalid_cash_instrument_format_fails_closed(value):
    master = InstrumentMaster()

    with pytest.raises((ValueError, LookupError)):
        _normalise_cash_instrument(master, value, "NSE")


def test_resolved_non_nse_cash_instrument_fails_closed():
    class FakeMaster:
        def resolve_cash_instrument(self, tradingsymbol, exchange="NSE"):
            return {
                "symbol": tradingsymbol,
                "exch_seg": "BSE",
                "token": "500325",
            }

    with pytest.raises(LookupError, match="not NSE"):
        _normalise_cash_instrument(FakeMaster(), "RELIANCE-EQ", "NSE")
