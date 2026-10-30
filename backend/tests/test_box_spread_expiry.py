from datetime import date

from app.market_data.box_spread_expiry import classify_index_expiry_dates


def test_nifty_classifies_last_listed_expiry_in_month_as_monthly():
    expiries = [
        date(2026, 10, 6),
        date(2026, 10, 13),
        date(2026, 10, 20),
        date(2026, 10, 27),
        date(2026, 11, 3),
        date(2026, 11, 10),
    ]

    result = classify_index_expiry_dates("NIFTY", expiries)

    assert result[date(2026, 10, 6)] == "WEEKLY"
    assert result[date(2026, 10, 20)] == "WEEKLY"
    assert result[date(2026, 10, 27)] == "MONTHLY"
    assert result[date(2026, 11, 10)] == "MONTHLY"


def test_sensex_uses_weekly_cycle_and_monthly_last_expiry():
    expiries = [
        date(2026, 10, 1),
        date(2026, 10, 8),
        date(2026, 10, 15),
        date(2026, 10, 22),
        date(2026, 10, 29),
    ]

    result = classify_index_expiry_dates("SENSEX", expiries)

    assert result[date(2026, 10, 22)] == "WEEKLY"
    assert result[date(2026, 10, 29)] == "MONTHLY"


def test_monthly_only_indices_never_get_weekly_classification():
    expiries = [date(2026, 10, 6), date(2026, 10, 27)]

    for symbol in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "BANKEX"):
        result = classify_index_expiry_dates(symbol, expiries)
        assert set(result.values()) == {"MONTHLY"}


def test_expiry_classification_normalizes_symbol_and_deduplicates_dates():
    expiry = date(2026, 10, 27)

    result = classify_index_expiry_dates(" nifty ", [expiry, expiry])

    assert result == {expiry: "MONTHLY"}
