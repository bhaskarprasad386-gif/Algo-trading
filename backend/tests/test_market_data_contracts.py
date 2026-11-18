from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord, OptionType

def test_market_data_record_has_stable_identity_and_typed_fields():
    record = MarketDataRecord(
        instrument=InstrumentKey("NSE", "NFO", "101"),
        symbol="NIFTY30SEP26FUT",
        instrument_type=InstrumentType.FUTURE,
        timestamp_ns=1_750_000_000_000_000_000,
        ltp=100.5, bid=100.4, ask=100.6, bid_qty=10, ask_qty=12,
        volume=1000, oi=500, expiry="2026-09-30", lot_size=65, tick_size=0.05,
        payload={"raw": "provider"},
    )
    assert record.identity == ("NSE", "NFO", "101", 1_750_000_000_000_000_000)
    assert record.is_executable_quote is True
    assert record.as_dict()["instrument_type"] == "future"
    assert record.as_dict()["payload"] == {"raw": "provider"}

def test_option_contract_requires_strike_and_option_type():
    record = MarketDataRecord(
        instrument=InstrumentKey("NSE", "NFO", "202"),
        symbol="NIFTY30SEP26CE", instrument_type=InstrumentType.OPTION,
        timestamp_ns=1, strike=25000, option_type=OptionType.CALL,
    )
    assert record.option_type is OptionType.CALL
    assert record.as_dict()["option_type"] == "CE"

def test_invalid_quote_is_rejected():
    try:
        MarketDataRecord(
            instrument=InstrumentKey("NSE", "NFO", "303"),
            symbol="BAD", instrument_type=InstrumentType.FUTURE,
            timestamp_ns=1, bid=101, ask=100,
        )
    except ValueError as exc:
        assert "bid cannot exceed ask" in str(exc)
    else:
        raise AssertionError("crossed quote must be rejected")

def test_option_metadata_cannot_leak_into_non_option_instruments():
    try:
        MarketDataRecord(
            instrument=InstrumentKey("MCX", "MCX", "404"),
            symbol="GOLD", instrument_type=InstrumentType.COMMODITY,
            timestamp_ns=1, option_type=OptionType.PUT,
        )
    except ValueError as exc:
        assert "only valid for option" in str(exc)
    else:
        raise AssertionError("option metadata on a non-option record must fail")

def test_instrument_key_requires_exchange_segment_and_token():
    for kwargs in (
        {"exchange": "", "segment": "NFO", "token": "1"},
        {"exchange": "NSE", "segment": "", "token": "1"},
        {"exchange": "NSE", "segment": "NFO", "token": ""},
    ):
        try:
            InstrumentKey(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("blank instrument identity must fail")

def test_option_requires_complete_metadata():
    for kwargs in (
        {"strike": None, "option_type": OptionType.CALL},
        {"strike": 25000, "option_type": None},
    ):
        try:
            MarketDataRecord(
                instrument=InstrumentKey("NSE", "NFO", "505"),
                symbol="INCOMPLETE", instrument_type=InstrumentType.OPTION,
                timestamp_ns=1, **kwargs,
            )
        except ValueError as exc:
            assert "require strike and option_type" in str(exc)
        else:
            raise AssertionError("incomplete option metadata must fail")

def test_record_rejects_invalid_timestamp_and_nonfinite_price():
    for timestamp_ns in (-1, True):
        try:
            MarketDataRecord(
                instrument=InstrumentKey("NSE", "NFO", "606"),
                symbol="BADTIME", instrument_type=InstrumentType.FUTURE,
                timestamp_ns=timestamp_ns,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid timestamp must fail")
    try:
        MarketDataRecord(
            instrument=InstrumentKey("NSE", "NFO", "707"),
            symbol="BADPRICE", instrument_type=InstrumentType.FUTURE,
            timestamp_ns=1, ltp=float("nan"),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("non-finite price must fail")
