import pytest

from app.market_data.instruments import InstrumentMaster


def master_with(rows):
    master = InstrumentMaster()
    master.instruments = rows
    return master


def test_resolve_cash_instrument_accepts_equity_variants():
    master = master_with([
        {"exch_seg": "NSE", "instrumenttype": "EQ", "symbol": "RELIANCE-EQ", "token": "2885"},
    ])

    instrument = master.resolve_cash_instrument("reliance-eq")

    assert instrument["token"] == "2885"
    assert master.resolve_cash_token("RELIANCE-EQ") == "2885"


def test_resolve_cash_instrument_accepts_empty_instrument_type():
    master = master_with([
        {"exch_seg": "NSE", "instrumenttype": "", "symbol": "SBIN-EQ", "token": "3045"},
    ])

    assert master.resolve_cash_token("SBIN-EQ") == "3045"


def test_resolve_cash_instrument_fails_closed_on_missing_symbol():
    master = master_with([])

    with pytest.raises(LookupError, match="found 0"):
        master.resolve_cash_instrument("MISSING-EQ")


def test_resolve_cash_instrument_fails_closed_on_ambiguous_symbol():
    master = master_with([
        {"exch_seg": "NSE", "instrumenttype": "EQ", "symbol": "ABC-EQ", "token": "1"},
        {"exch_seg": "NSE", "instrumenttype": "CASH", "symbol": "ABC-EQ", "token": "2"},
    ])

    with pytest.raises(LookupError, match="found 2"):
        master.resolve_cash_instrument("ABC-EQ")


def test_resolve_cash_instrument_rejects_non_cash_instrument():
    master = master_with([
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "symbol": "ABC-EQ", "token": "9"},
    ])

    with pytest.raises(LookupError, match="found 0"):
        master.resolve_cash_instrument("ABC-EQ")


def test_resolve_cash_instrument_rejects_missing_token():
    master = master_with([
        {"exch_seg": "NSE", "instrumenttype": "EQ", "symbol": "ABC-EQ", "token": ""},
    ])

    with pytest.raises(LookupError, match="has no Angel One token"):
        master.resolve_cash_instrument("ABC-EQ")
