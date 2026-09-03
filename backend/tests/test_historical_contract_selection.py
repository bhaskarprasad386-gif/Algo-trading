from datetime import date

from app.scanner.full_fno_backtest import historical_current_near_contracts


def test_historical_current_and_near_use_expiry_order():
    contracts = [
        ("MAR", date(2026, 3, 26)),
        ("APR", date(2026, 4, 30)),
        ("MAY", date(2026, 5, 28)),
    ]

    current, near = historical_current_near_contracts(contracts, date(2026, 4, 1))

    assert current == ("APR", date(2026, 4, 30))
    assert near == ("MAY", date(2026, 5, 28))


def test_historical_contract_selection_has_no_future_contract_after_last_expiry():
    contracts = [("APR", date(2026, 4, 30))]

    current, near = historical_current_near_contracts(contracts, date(2026, 5, 1))

    assert current is None
    assert near is None
