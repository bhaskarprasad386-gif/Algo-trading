import math

import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _quotes():
    return (
        CashQuote(symbol="ABC", ltp=100.0, bid=99.9, ask=100.1),
        FutureQuote(
            symbol="ABC",
            contract_month="2026-10",
            ltp=104.0,
            lot_size=100,
            margin_required=20_000.0,
            volume=10_000,
            oi=20_000,
            bid=103.8,
            ask=104.2,
        ),
    )


def test_config_rejects_non_finite_numeric_values():
    for field in ("min_gap", "charges", "funding_cost", "gap_match_tolerance"):
        for value in (math.nan, math.inf, -math.inf):
            with pytest.raises(ValueError, match=field):
                CashFutureConfig(**{field: value})


def test_config_rejects_non_finite_optional_limits():
    for field in ("max_margin", "max_bid_ask_spread_pct", "max_cash_bid_ask_spread_pct"):
        with pytest.raises(ValueError, match=field):
            CashFutureConfig(**{field: math.inf})


def test_calculator_rejects_non_finite_ltp_and_margin():
    cash, future = _quotes()
    with pytest.raises(ValueError, match="cash ltp"):
        calculate_cash_future(
            CashQuote(symbol="ABC", ltp=math.nan, bid=99.9, ask=100.1),
            future,
            CashFutureConfig(),
        )
    with pytest.raises(ValueError, match="future ltp"):
        calculate_cash_future(
            cash,
            FutureQuote(
                symbol="ABC",
                contract_month="2026-10",
                ltp=math.inf,
                lot_size=100,
                margin_required=20_000.0,
            ),
            CashFutureConfig(),
        )
    with pytest.raises(ValueError, match="margin_required"):
        calculate_cash_future(
            cash,
            FutureQuote(
                symbol="ABC",
                contract_month="2026-10",
                ltp=104.0,
                lot_size=100,
                margin_required=math.inf,
            ),
            CashFutureConfig(),
        )
