from datetime import datetime

import pytest
from zoneinfo import ZoneInfo

from app.scanner.iv_skew_flip.engine import (
    OptionQuote, Signal, black_scholes_price, confluence, evaluate_flip,
    evaluate_gamma_blast, evaluate_skew, implied_volatility,
)


def test_implied_volatility_round_trip_for_call_and_put():
    for kind in ("CE", "PE"):
        premium = black_scholes_price(kind, 22000, 22000, 7 / 365, 0.22)
        recovered = implied_volatility(kind, 22000, 22000, 7 / 365, premium)
        assert recovered is not None
        assert abs(recovered - 0.22) < 1e-6


def test_implied_volatility_rejects_invalid_arbitrage_price():
    assert implied_volatility("CE", 22000, 22000, 7 / 365, 30000) is None


def test_gamma_blast_requires_oi_drop_and_premium_rise():
    prior = OptionQuote("NIFTY", "2026-11-12", 22000, "CE", 1, 100, oi=100000)
    current = OptionQuote("NIFTY", "2026-11-12", 22000, "CE", 2, 112, oi=90000)
    signal = evaluate_gamma_blast(current, prior)
    assert signal.status == "SIGNAL"
    assert signal.direction == "BULLISH"

@pytest.mark.parametrize(
    "field,value",
    [
        ("underlying", "BANKNIFTY"),
        ("expiry", "2026-11-19"),
        ("strike", 22050),
        ("option_type", "PE"),
        ("token", "OTHER"),
        ("token", ""),
    ],
)
def test_gamma_blast_rejects_reference_from_different_contract(field, value):
    from dataclasses import replace

    prior = OptionQuote("NIFTY", "2026-11-12", 22000, "CE", 1, 100, oi=100000, token="N1")
    current = OptionQuote("NIFTY", "2026-11-12", 22000, "CE", 2, 112, oi=90000, token="N1")
    mismatched_prior = replace(prior, **{field: value})
    result = evaluate_gamma_blast(current, mismatched_prior)
    assert result.status == "WAITING"
    assert "same option contract" in result.reason


def test_gamma_blast_rejects_non_chronological_reference_sample():
    prior = OptionQuote("NIFTY", "2026-11-12", 22000, "CE", 2, 100, oi=100000, token="N1")
    current = OptionQuote("NIFTY", "2026-11-12", 22000, "CE", 2, 112, oi=90000, token="N1")
    result = evaluate_gamma_blast(current, prior)
    assert result.status == "WAITING"
    assert "ordered observations" in result.reason

def _synthetic_chain(call_iv=0.24, put_iv=0.20):
    from app.scanner.iv_skew_flip.engine import DEFAULT_CONFIGS, _expiry_years

    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    now_ns = 1_800_000_000_000_000_000
    config = DEFAULT_CONFIGS[0]
    years = _expiry_years("12NOV2026", now)
    assert years is not None
    quotes = []
    for strike in (21900, 21950, 22000, 22050, 22100):
        for side, volatility in (("CE", call_iv), ("PE", put_iv)):
            premium = black_scholes_price(side, 22000, strike, years, volatility)
            quotes.append(OptionQuote("NIFTY", "12NOV2026", strike, side, now_ns - 1_000_000_000, premium, token=f"{strike}-{side}"))
    return quotes, now, now_ns, config


def test_iv_skew_generates_bullish_signal_from_synthetic_chain():
    quotes, now, now_ns, config = _synthetic_chain()
    signal = evaluate_skew(quotes, 22000, now, now_ns, config)
    assert signal.status == "SIGNAL"
    assert signal.direction == "BULLISH"
    assert signal.value is not None and signal.value > config.bullish_skew_threshold


def test_iv_flip_detects_confirmed_atm_crossover():
    quotes, now, now_ns, config = _synthetic_chain()
    signal = evaluate_flip(quotes, 22000, now, now_ns, config, previous_atm_diff=-0.20)
    assert signal.status == "SIGNAL"
    assert signal.direction == "BULLISH"


def test_iv_flip_without_prior_opposite_side_does_not_signal():
    quotes, now, now_ns, config = _synthetic_chain()
    signal = evaluate_flip(quotes, 22000, now, now_ns, config, previous_atm_diff=None)
    assert signal.status == "NEUTRAL"
    assert signal.direction is None


def test_confluence_requires_two_same_direction_signals():
    rows = [
        Signal("IV_SKEW", "BULLISH", 1.0, 0.8, "SIGNAL", "test"),
        Signal("IV_FLIP", "BULLISH", 0.2, 0.15, "SIGNAL", "test"),
        Signal("GAMMA_BLAST", "BEARISH", 6.0, 5.0, "SIGNAL", "test"),
    ]
    result = confluence(rows)
    assert result.status == "SIGNAL"
    assert result.direction == "BULLISH"


def test_confluence_does_not_mix_opposite_directions():
    rows = [
        Signal("IV_SKEW", "BULLISH", 1.0, 0.8, "SIGNAL", "test"),
        Signal("IV_FLIP", "BEARISH", -0.2, 0.15, "SIGNAL", "test"),
        Signal("GAMMA_BLAST", None, None, None, "NEUTRAL", "test"),
    ]
    assert confluence(rows).status == "NEUTRAL"


def test_expiry_years_accepts_angel_one_master_formats():
    from app.scanner.iv_skew_flip.engine import _expiry_years

    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    expected = _expiry_years("2026-11-12", now)
    assert expected is not None and expected > 0
    for expiry in ("12NOV2026", "12NOV26", "12-Nov-2026", "12-Nov-26"):
        value = _expiry_years(expiry, now)
        assert value is not None
        assert abs(value - expected) < 1e-10
