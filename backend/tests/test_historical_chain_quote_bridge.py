import pytest

from app.backtesting.historical_chain_quote_bridge import future_quote, normalize_chain_payload, option_quote, option_quotes
from app.backtesting.arbitrage_chain_selector import ChainContract


def _contract(timestamp=1, strike=100, option_type="CE", venue="NSE"):
    return ChainContract(
        timestamp_ns=timestamp, venue=venue, underlying="ABC", instrument_class="INDEX",
        expiry=20260924, strike=strike, option_type=option_type,
        lot_size=50, volume=1000, oi=5000, bid=10, ask=11,
    )


def test_option_quote_preserves_real_ce_pe_identity():
    quote = option_quote((_contract(option_type="CE"), _contract(option_type="PE", strike=100)))
    assert quote["timestamp_ns"] == 1
    assert quote["underlying"] == "ABC"
    assert quote["expiry"] == 20260924
    assert quote["strike"] == 100
    assert quote["call_bid"] == 10
    assert quote["put_bid"] == 10
    assert quote["lot_size"] == 50
    assert quote["instrument_class"] == "INDEX"


def test_option_quotes_skip_incomplete_strikes_without_fabrication():
    quotes = option_quotes((_contract(strike=100, option_type="CE"), _contract(strike=100, option_type="PE"), _contract(strike=110, option_type="CE")))
    assert len(quotes) == 1
    assert quotes[0]["strike"] == 100


def test_option_quote_rejects_mismatched_timestamp():
    with pytest.raises(ValueError, match="share timestamp"):
        option_quote((_contract(timestamp=1, option_type="CE"), _contract(timestamp=2, option_type="PE")))


def test_future_quote_requires_genuine_liquidity_fields():
    quote = future_quote({"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "bid": 120, "ask": 121, "lot_size": 50, "instrument_class": "INDEX", "volume": 5000, "oi": 10000})
    assert quote["expiry"] == 20260924
    assert quote["volume"] == 5000
    assert quote["oi"] == 10000


def test_future_quote_rejects_missing_liquidity_fields():
    with pytest.raises(ValueError, match="volume"):
        future_quote({"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "bid": 120, "ask": 121, "lot_size": 50, "instrument_class": "INDEX"})


def test_normalize_chain_payload_requires_complete_raw_option_contracts():
    raw = {"contracts": [
        {"timestamp_ns": 1, "venue": "NSE", "underlying": "ABC", "instrument_class": "INDEX", "expiry": 20260924, "strike": 100, "option_type": "CE", "lot_size": 50, "volume": 1000, "oi": 5000, "bid": 10, "ask": 11},
        {"timestamp_ns": 1, "venue": "NSE", "underlying": "ABC", "instrument_class": "INDEX", "expiry": 20260924, "strike": 100, "option_type": "PE", "lot_size": 50, "volume": 1000, "oi": 5000, "bid": 12, "ask": 13},
    ]}
    quote = normalize_chain_payload(raw, option=True)
    assert quote["call_ask"] == 11
    assert quote["put_ask"] == 13


def test_normalize_chain_payload_rejects_incomplete_raw_option_contract():
    with pytest.raises(ValueError, match="chain contract missing required fields"):
        normalize_chain_payload({"contracts": [{"timestamp_ns": 1, "option_type": "CE"}]}, option=True)
