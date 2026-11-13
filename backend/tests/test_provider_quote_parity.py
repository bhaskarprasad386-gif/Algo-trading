from datetime import date
from decimal import Decimal

from app.market_data.provider_contracts import CanonicalContractKey
from app.market_data.provider_quote_quality import ProviderQuoteObservation, QuoteQualityState
from app.market_data.provider_quote_parity import analyze_quote_parity

NOW_MONOTONIC = 90_000_000_000
NOW_EXCHANGE = 1_800_000_000_000_000_000
MAX_AGE = 5_000_000_000


def _contract(strike="22000"):
    return CanonicalContractKey(
        exchange="NFO", segment="OPT", underlying="NIFTY",
        instrument_type="OPTION", expiry=date(2026, 11, 19),
        strike=Decimal(str(strike)), option_type="CE",
    )


def _quote(provider, *, bid="100.00", ask="100.10", bid_size=25, ask_size=30,
           exchange_timestamp=NOW_EXCHANGE - 1_000_000_000,
           received=NOW_MONOTONIC - 1_000_000_000, contract=None):
    return ProviderQuoteObservation(
        provider=provider, contract=contract or _contract(), generation=1,
        authenticated=True, connection_current=True,
        exchange_timestamp_ns=exchange_timestamp, received_monotonic_ns=received,
        bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size, depth_complete=True,
    )


def test_parity_metrics_measure_price_size_and_timestamp_skew():
    result = analyze_quote_parity(
        _quote("angel", bid="100.00", ask="100.10", bid_size=25, ask_size=30,
               exchange_timestamp=NOW_EXCHANGE - 1_000_000_000),
        _quote("shoonya", bid="100.05", ask="100.15", bid_size=20, ask_size=36,
               exchange_timestamp=NOW_EXCHANGE - 1_200_000_000),
        now_monotonic_ns=NOW_MONOTONIC, now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=MAX_AGE,
    )
    assert result.quality.state is QuoteQualityState.COMPARABLE
    assert result.bid_abs_diff == Decimal("0.05")
    assert result.ask_abs_diff == Decimal("0.05")
    assert result.bid_size_abs_diff == Decimal("5")
    assert result.ask_size_abs_diff == Decimal("6")
    assert result.mid_abs_diff == Decimal("0.05")
    assert result.exchange_timestamp_skew_ns == 200_000_000
    assert result.mid_relative_diff_bps is not None


def test_identity_mismatch_suppresses_all_price_parity_metrics():
    result = analyze_quote_parity(
        _quote("angel"), _quote("shoonya", contract=_contract("22100")),
        now_monotonic_ns=NOW_MONOTONIC, now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=MAX_AGE,
    )
    assert result.quality.state is QuoteQualityState.IDENTITY_MISMATCH
    assert result.bid_abs_diff is None
    assert result.mid_abs_diff is None
    assert result.exchange_timestamp_skew_ns is None


def test_stale_quotes_keep_diagnostic_metrics_but_never_become_fresh():
    result = analyze_quote_parity(
        _quote("angel", received=NOW_MONOTONIC - MAX_AGE - 1),
        _quote("shoonya", received=NOW_MONOTONIC - MAX_AGE - 2),
        now_monotonic_ns=NOW_MONOTONIC, now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=MAX_AGE,
    )
    assert result.quality.state is QuoteQualityState.BOTH_STALE
    assert result.bid_abs_diff == Decimal("0.00")


def test_missing_or_invalid_side_suppresses_only_that_side_metric():
    result = analyze_quote_parity(
        _quote("angel", bid=None, ask="100.10"),
        _quote("shoonya", bid="100.05", ask="100.15"),
        now_monotonic_ns=NOW_MONOTONIC, now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=MAX_AGE,
    )
    assert result.bid_abs_diff is None
    assert result.ask_abs_diff == Decimal("0.05")
    assert result.mid_abs_diff is None