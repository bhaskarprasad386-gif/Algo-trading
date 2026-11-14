from dataclasses import replace
from decimal import Decimal

import pytest

from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord, OptionType
from app.market_data.provider_quote_adapter import observation_from_market_data_record
from app.market_data.normalizer import AngelOneTickNormalizer
from app.market_data.shoonya_normalizer import ShoonyaTickNormalizer
from app.market_data.registry import InstrumentDescriptor
from app.market_data.provider_quote_quality import QuoteQualityState, assess_quote, compare_provider_quotes


NOW_MONOTONIC = 80_000_000_000
NOW_EXCHANGE = 1_800_000_000_000_000_000
MAX_AGE = 3_000_000_000


def _record(provider):
    token = "ANGEL-123" if provider == "angel_one" else "SHOONYA-987"
    symbol = "NIFTY26NOV22000CE" if provider == "angel_one" else "NIFTY 19 NOV 22000 CE"
    return MarketDataRecord(
        instrument=InstrumentKey("NFO", "OPT", token),
        symbol=symbol,
        instrument_type=InstrumentType.OPTION,
        timestamp_ns=NOW_EXCHANGE - 500_000_000,
        ltp=100.02,
        bid=100.00,
        ask=100.05,
        bid_qty=25,
        ask_qty=30,
        underlying="NIFTY",
        expiry="2026-11-19",
        strike=22000.0,
        option_type=OptionType.CALL,
        lot_size=65,
        tick_size=0.05,
    )


def _observation(provider, *, depth_complete=True, record=None):
    return observation_from_market_data_record(
        provider,
        record or _record(provider),
        generation=4,
        authenticated=True,
        connection_current=True,
        received_monotonic_ns=NOW_MONOTONIC - 500_000_000,
        depth_complete=depth_complete,
    )


def test_angel_and_shoonya_records_map_to_same_contract_despite_different_tokens_symbols():
    angel = _observation("angel_one")
    shoonya = _observation("shoonya")
    assert angel.contract == shoonya.contract
    assert angel.contract.exchange == "NFO"
    assert angel.contract.segment == "OPT"
    assert angel.contract.strike == Decimal("22000")
    result = compare_provider_quotes(
        angel, shoonya,
        now_monotonic_ns=NOW_MONOTONIC,
        now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=MAX_AGE,
    )
    assert result.state is QuoteQualityState.COMPARABLE


def test_different_expiry_or_strike_never_compares_as_same_contract():
    changed = _record("shoonya")
    changed = replace(changed, strike=22100.0)
    result = compare_provider_quotes(
        _observation("angel_one"), _observation("shoonya", record=changed),
        now_monotonic_ns=NOW_MONOTONIC,
        now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=MAX_AGE,
    )
    assert result.state is QuoteQualityState.IDENTITY_MISMATCH


def test_top_of_book_does_not_claim_full_depth_by_default():
    observation = _observation("angel_one", depth_complete=False)
    result = assess_quote(
        observation,
        now_monotonic_ns=NOW_MONOTONIC,
        now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=MAX_AGE,
    )
    assert result.valid is False
    assert "depth_incomplete" in result.reasons


def test_missing_normalized_exchange_timestamp_is_rejected():
    record = _record("angel_one")
    record = replace(record, timestamp_ns=0)
    observation = _observation("angel_one", record=record)
    result = assess_quote(
        observation,
        now_monotonic_ns=NOW_MONOTONIC,
        now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=MAX_AGE,
    )
    assert result.valid is False
    assert "exchange_timestamp_missing" in result.reasons


def test_adapter_rejects_unknown_provider_and_invalid_record_type():
    with pytest.raises(ValueError, match="provider"):
        observation_from_market_data_record(
            "other", _record("angel_one"), generation=1, authenticated=True,
            connection_current=True, received_monotonic_ns=NOW_MONOTONIC,
        )
    with pytest.raises(TypeError, match="MarketDataRecord"):
        observation_from_market_data_record(
            "angel_one", {}, generation=1, authenticated=True,
            connection_current=True, received_monotonic_ns=NOW_MONOTONIC,
        )


def test_adapter_rejects_malformed_option_expiry_instead_of_truncating_it():
    record = replace(_record("angel_one"), expiry="2026-11-19-not-a-date")
    with pytest.raises(ValueError):
        _observation("angel_one", record=record)


def test_real_normalizer_shapes_feed_same_read_only_parity_adapter():
    angel_descriptor = InstrumentDescriptor(
        key=InstrumentKey("NFO", "OPT", "ANGEL-123"),
        symbol="NIFTY26NOV22000CE", instrument_type="option",
        exchange="NFO", segment="OPT", expiry="2026-11-19",
        strike=22000.0, option_type="CE", lot_size=65,
        tick_size=0.05, underlying="NIFTY",
    )
    shoonya_descriptor = InstrumentDescriptor(
        key=InstrumentKey("NFO", "OPT", "SHOONYA-987"),
        symbol="NIFTY 19 NOV 22000 CE", instrument_type="option",
        exchange="NFO", segment="OPT", expiry="2026-11-19",
        strike=22000.0, option_type="CE", lot_size=65,
        tick_size=0.05, underlying="NIFTY",
    )
    angel_record = AngelOneTickNormalizer(price_scale=100).normalize(
        angel_descriptor,
        {
            "token": "ANGEL-123",
            "exchange_timestamp": 1_800_000_000_000,
            "last_traded_price": 10_002,
            "buy": [{"price": 10_000, "quantity": 25}],
            "sell": [{"price": 10_005, "quantity": 30}],
        },
    )
    shoonya_record = ShoonyaTickNormalizer().normalize(
        shoonya_descriptor,
        {
            "tk": "SHOONYA-987", "e": "NFO",
            "ts": "NIFTY 19 NOV 22000 CE", "ft": "1800000000",
            "lp": "100.02", "bp1": "100.00", "sp1": "100.05",
            "bq1": "25", "sq1": "30",
        },
    )
    angel = observation_from_market_data_record(
        "angel_one", angel_record, generation=8, authenticated=True,
        connection_current=True, received_monotonic_ns=NOW_MONOTONIC - 500_000_000,
        depth_complete=True,
    )
    shoonya = observation_from_market_data_record(
        "shoonya", shoonya_record, generation=11, authenticated=True,
        connection_current=True, received_monotonic_ns=NOW_MONOTONIC - 500_000_000,
        depth_complete=True,
    )
    result = compare_provider_quotes(
        angel, shoonya, now_monotonic_ns=NOW_MONOTONIC,
        now_exchange_timestamp_ns=NOW_EXCHANGE, max_age_ns=MAX_AGE,
    )
    assert angel.contract == shoonya.contract
    assert angel.bid == shoonya.bid == 100.0
    assert angel.ask == shoonya.ask == 100.05
    assert result.state is QuoteQualityState.COMPARABLE
