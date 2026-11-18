import math

import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _quotes():
    return (CashQuote(symbol="ABC", ltp=100.0, bid=99.9, ask=100.1), FutureQuote(symbol="ABC", contract_month="2026-10", ltp=104.0, lot_size=100, margin_required=20_000.0, volume=10_000, oi=20_000, bid=103.8, ask=104.2))


def test_symbol_mismatch_is_never_executable():
    cash, future = _quotes()
    future = FutureQuote(**{**future.__dict__, "symbol": "XYZ"})
    result = calculate_cash_future(cash, future, CashFutureConfig())
    assert result.executable is False
    assert result.rejection_reasons == ("symbol_mismatch",)


def test_symbol_matching_is_case_and_whitespace_tolerant():
    cash, future = _quotes()
    cash = CashQuote(symbol=" abc ", ltp=cash.ltp, bid=cash.bid, ask=cash.ask)
    future = FutureQuote(**{**future.__dict__, "symbol": "ABC"})
    result = calculate_cash_future(cash, future, CashFutureConfig())
    assert result.executable is True


def test_config_rejects_non_finite_numeric_values():
    for field in ("min_gap", "charges", "funding_cost", "gap_match_tolerance"):
        for value in (math.nan, math.inf, -math.inf):
            with pytest.raises(ValueError, match=field): CashFutureConfig(**{field: value})


def test_config_rejects_non_finite_optional_limits():
    for field in ("max_margin", "max_bid_ask_spread_pct", "max_cash_bid_ask_spread_pct"):
        with pytest.raises(ValueError, match=field): CashFutureConfig(**{field: math.inf})


@pytest.mark.parametrize("field", ["min_volume", "min_oi", "min_days_to_expiry", "history_days", "graph_days", "max_days_to_expiry"])
def test_config_integer_fields_must_be_strict_non_negative_integers(field):
    for value in (1.5, True, False):
        with pytest.raises(ValueError, match=f"{field}.*non-negative integer"):
            CashFutureConfig(**{field: value})
    with pytest.raises(ValueError, match=f"{field} cannot be negative"):
        CashFutureConfig(**{field: -1})


def test_config_integer_fields_accept_zero_and_positive_integers():
    config = CashFutureConfig(min_volume=1, min_oi=2, min_days_to_expiry=3, max_days_to_expiry=30, history_days=365, graph_days=30)
    assert config.min_volume == 1
    assert config.min_oi == 2
    assert config.min_days_to_expiry == 3


def test_calculator_rejects_non_finite_ltp_and_margin():
    cash, future = _quotes()
    with pytest.raises(ValueError, match="cash ltp"): calculate_cash_future(CashQuote("ABC", math.nan, 99.9, 100.1), future, CashFutureConfig())
    with pytest.raises(ValueError, match="future ltp"): calculate_cash_future(cash, FutureQuote("ABC", "2026-10", math.inf, 100, 20_000), CashFutureConfig())
    with pytest.raises(ValueError, match="margin_required"): calculate_cash_future(cash, FutureQuote("ABC", "2026-10", 104.0, 100, math.inf), CashFutureConfig())


@pytest.mark.parametrize(("field", "value"), [(f, v) for f in ("cash bid", "cash ask", "future bid", "future ask") for v in (math.nan, math.inf, -math.inf)])
def test_calculator_rejects_non_finite_bid_ask_values(field, value):
    cash, future = _quotes()
    if field == "cash bid": cash = CashQuote("ABC", 100.0, value, 100.1)
    elif field == "cash ask": cash = CashQuote("ABC", 100.0, 99.9, value)
    elif field == "future bid": future = FutureQuote(**{**future.__dict__, "bid": value})
    else: future = FutureQuote(**{**future.__dict__, "ask": value})
    with pytest.raises(ValueError, match=field): calculate_cash_future(cash, future, CashFutureConfig())


@pytest.mark.parametrize(("kind", "value"), [(k, v) for k in ("cash bid", "cash ask", "future bid", "future ask") for v in (0.0, -0.01)])
def test_calculator_never_marks_non_positive_quotes_executable(kind, value):
    cash, future = _quotes()
    if kind == "cash bid": cash = CashQuote("ABC", 100.0, value, 100.1)
    elif kind == "cash ask": cash = CashQuote("ABC", 100.0, 99.9, value)
    elif kind == "future bid": future = FutureQuote(**{**future.__dict__, "bid": value})
    else: future = FutureQuote(**{**future.__dict__, "ask": value})
    result = calculate_cash_future(cash, future, CashFutureConfig())
    assert result.executable is False
    assert ("invalid_cash_bid_ask" if kind.startswith("cash") else "invalid_future_bid_ask") in result.rejection_reasons


@pytest.mark.parametrize("kind", ["cash", "future"])
def test_calculator_rejects_reversed_bid_ask(kind):
    cash, future = _quotes()
    if kind == "cash": cash = CashQuote("ABC", 100.0, 100.5, 100.1)
    else: future = FutureQuote(**{**future.__dict__, "bid": 104.5, "ask": 104.2})
    result = calculate_cash_future(cash, future, CashFutureConfig())
    assert result.executable is False
    assert ("invalid_cash_bid_ask" if kind == "cash" else "invalid_future_bid_ask") in result.rejection_reasons


@pytest.mark.parametrize("kind", ["cash", "future"])
def test_calculator_rejects_ltp_outside_complete_quote(kind):
    cash, future = _quotes()
    if kind == "cash": cash = CashQuote("ABC", 100.2, 99.9, 100.1)
    else: future = FutureQuote(**{**future.__dict__, "ltp": 104.5})
    result = calculate_cash_future(cash, future, CashFutureConfig())
    assert result.executable is False
    assert f"{kind}_ltp_outside_bid_ask" in result.rejection_reasons


def test_rejection_reasons_are_unique_after_all_hardening_checks():
    cash, future = _quotes()
    result = calculate_cash_future(cash, future, CashFutureConfig(max_bid_ask_spread_pct=0.01, max_cash_bid_ask_spread_pct=0.01))
    assert len(result.rejection_reasons) == len(set(result.rejection_reasons))


def test_calculator_keeps_valid_two_sided_quotes_executable():
    cash, future = _quotes()
    result = calculate_cash_future(cash, future, CashFutureConfig())
    assert result.executable is True
    assert result.executable_gap == pytest.approx(3.7)
    assert result.cash_execution_price == pytest.approx(100.1)
    assert result.future_execution_price == pytest.approx(103.8)
