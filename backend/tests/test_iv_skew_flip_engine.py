from datetime import datetime
from zoneinfo import ZoneInfo

from app.scanner.iv_skew_flip.engine import (
    OptionQuote, Signal, black_scholes_price, confluence, evaluate_gamma_blast,
    implied_volatility,
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
