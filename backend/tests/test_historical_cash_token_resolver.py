import pytest

from app.backtesting.historical_cash_token_resolver import HistoricalCashTokenResolver


def test_resolves_nse_equity_token_by_underlying():
    resolver = HistoricalCashTokenResolver(
        [
            {"exch_seg": "NSE", "symbol": "ABC-EQ", "name": "ABC", "token": "123", "instrumenttype": ""},
            {"exch_seg": "NFO", "symbol": "ABC26SEP", "name": "ABC", "token": "999", "instrumenttype": "FUTSTK"},
        ]
    )

    selected = resolver.resolve("abc")

    assert selected.token == "123"
    assert selected.symbol == "ABC-EQ"
    assert selected.instrument == "NSE:123:ABC-EQ"


def test_non_equity_nse_rows_are_ignored():
    resolver = HistoricalCashTokenResolver(
        [{"exch_seg": "NSE", "symbol": "ABC", "name": "ABC", "token": "123", "instrumenttype": "INDEX"}]
    )

    with pytest.raises(LookupError):
        resolver.resolve("ABC")


def test_missing_cash_token_fails_closed():
    resolver = HistoricalCashTokenResolver([])

    with pytest.raises(LookupError):
        resolver.resolve_token("ABC")


def test_conflicting_cash_tokens_fail_closed():
    with pytest.raises(ValueError, match="ambiguous"):
        HistoricalCashTokenResolver(
            [
                {"exch_seg": "NSE", "symbol": "ABC-EQ", "name": "ABC", "token": "123", "instrumenttype": ""},
                {"exch_seg": "NSE", "symbol": "ABC-EQ", "name": "ABC", "token": "456", "instrumenttype": ""},
            ]
        )
