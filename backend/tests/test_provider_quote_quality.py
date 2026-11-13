from datetime import date
from decimal import Decimal

import pytest

from app.market_data.provider_contracts import CanonicalContractKey
from app.market_data.provider_quote_quality import (
    ProviderQuoteObservation,
    QuoteQualityState,
    assess_quote,
    compare_provider_quotes,
)


NOW = 50_000_000_000
MAX_AGE = 5_000_000_000
EXCHANGE_NOW = 1_800_000_000_000_000_000


def _contract(strike="22000"):
    return CanonicalContractKey(
        exchange="NFO",
        segment="OPT",
        underlying="NIFTY",
        instrument_type="OPTION",
        expiry=date(2026, 11, 19),
        strike=Decimal(str(strike)),
        option_type="CE",
    )


def _quote(provider, *, contract=None, received=NOW - 1_000_000_000, **overrides):
    values = {
        "provider": provider,
        "contract": contract or _contract(),
        "generation": 3,
        "authenticated": True,
        "connection_current": True,
        # Epoch timestamp intentionally differs from monotonic receive clock.
        "exchange_timestamp_ns": EXCHANGE_NOW - 1_000_000_000,
        "received_monotonic_ns": received,
        "bid": "100.00",
        "ask": "100.05",
        "bid_size": 25,
        "ask_size": 30,
        "depth_complete": True,
    }
    values.update(overrides)
    return ProviderQuoteObservation(**values)


def test_fresh_same_contract_quotes_are_comparable_without_source_selection():
    result = compare_provider_quotes(
        _quote("angel"), _quote("shoonya"),
        now_monotonic_ns=NOW, now_exchange_timestamp_ns=EXCHANGE_NOW, max_age_ns=MAX_AGE,
    )
    assert result.state is QuoteQualityState.COMPARABLE
    assert result.left.fresh and result.right.fresh
    assert result.identity_match is True


def test_contract_mismatch_is_explicit_even_when_both_quotes_are_fresh():
    result = compare_provider_quotes(
        _quote("angel"),
        _quote("shoonya", contract=_contract("22100")),
        now_monotonic_ns=NOW, now_exchange_timestamp_ns=EXCHANGE_NOW, max_age_ns=MAX_AGE,
    )
    assert result.state is QuoteQualityState.IDENTITY_MISMATCH
    assert result.identity_match is False


def test_single_fresh_provider_is_reported_without_promoting_it_to_primary():
    result = compare_provider_quotes(
        _quote("angel"),
        _quote("shoonya", received=NOW - MAX_AGE - 1),
        now_monotonic_ns=NOW, now_exchange_timestamp_ns=EXCHANGE_NOW, max_age_ns=MAX_AGE,
    )
    assert result.state is QuoteQualityState.SINGLE_SOURCE_FRESH
    assert result.left.fresh is True
    assert result.right.fresh is False
    assert "quote_stale" in result.right.reasons


def test_both_stale_valid_quotes_are_distinguished_from_invalid_quotes():
    result = compare_provider_quotes(
        _quote("angel", received=NOW - MAX_AGE - 1),
        _quote("shoonya", received=NOW - MAX_AGE - 2),
        now_monotonic_ns=NOW, max_age_ns=MAX_AGE,
    )
    assert result.state is QuoteQualityState.BOTH_STALE
    assert result.left.valid and result.right.valid


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"bid": 0}, "invalid_bid"),
        ({"ask": "NaN"}, "invalid_ask"),
        ({"bid": 101, "ask": 100}, "crossed_market"),
        ({"bid_size": 0}, "invalid_bid_size"),
        ({"ask_size": None}, "invalid_ask_size"),
        ({"authenticated": False}, "session_not_authenticated"),
        ({"connection_current": False}, "connection_generation_not_current"),
        ({"exchange_timestamp_ns": None}, "exchange_timestamp_missing"),
    ],
)
def test_invalid_quote_fields_are_reported(override, reason):
    result = assess_quote(
        _quote("angel", **override),
        now_monotonic_ns=NOW,
        now_exchange_timestamp_ns=EXCHANGE_NOW,
        max_age_ns=MAX_AGE,
    )
    assert result.valid is False
    assert reason in result.reasons


def test_future_local_receive_time_is_not_mistaken_for_freshness():
    result = assess_quote(
        _quote("angel", received=NOW + 1),
        now_monotonic_ns=NOW,
        now_exchange_timestamp_ns=EXCHANGE_NOW,
        max_age_ns=MAX_AGE,
    )
    assert result.valid is False
    assert "receive_time_in_future" in result.reasons


def test_age_uses_monotonic_receive_clock_not_exchange_epoch_timestamp():
    result = assess_quote(
        _quote("angel", exchange_timestamp_ns=EXCHANGE_NOW - 2_000_000_000),
        now_monotonic_ns=NOW,
        now_exchange_timestamp_ns=EXCHANGE_NOW,
        max_age_ns=MAX_AGE,
    )
    assert result.fresh is True
    assert result.age_ns == 1_000_000_000
    assert result.exchange_age_ns == 2_000_000_000


def test_invalid_clock_limits_are_rejected():
    quote = _quote("angel")
    with pytest.raises(ValueError, match="now_monotonic_ns"):
        assess_quote(quote, now_monotonic_ns=-1, now_exchange_timestamp_ns=EXCHANGE_NOW, max_age_ns=MAX_AGE)
    with pytest.raises(ValueError, match="max_age_ns"):
        assess_quote(quote, now_monotonic_ns=NOW, now_exchange_timestamp_ns=EXCHANGE_NOW, max_age_ns=0)

def test_exchange_timestamp_age_is_checked_separately_from_local_receive_age():
    stale = assess_quote(
        _quote("angel", exchange_timestamp_ns=EXCHANGE_NOW - MAX_AGE - 1),
        now_monotonic_ns=NOW,
        now_exchange_timestamp_ns=EXCHANGE_NOW,
        max_age_ns=MAX_AGE,
    )
    assert stale.valid is True
    assert stale.fresh is False
    assert "quote_stale" in stale.reasons

    future = assess_quote(
        _quote("angel", exchange_timestamp_ns=EXCHANGE_NOW + 1),
        now_monotonic_ns=NOW,
        now_exchange_timestamp_ns=EXCHANGE_NOW,
        max_age_ns=MAX_AGE,
    )
    assert future.valid is False
    assert "exchange_timestamp_in_future" in future.reasons


def test_incomplete_depth_is_not_reported_as_fully_valid_quote():
    result = assess_quote(
        _quote("angel", depth_complete=False),
        now_monotonic_ns=NOW,
        now_exchange_timestamp_ns=EXCHANGE_NOW,
        max_age_ns=MAX_AGE,
    )
    assert result.valid is False
    assert "depth_incomplete" in result.reasons
