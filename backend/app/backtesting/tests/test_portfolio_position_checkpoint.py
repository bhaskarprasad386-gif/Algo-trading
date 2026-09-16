import pytest

from app.backtesting.portfolio import Portfolio


def test_restore_state_rejects_fractional_position_quantity_without_coercion():
    portfolio = Portfolio(100_000)
    state = {
        "cash": 99_000.0,
        "realized_pnl": 0.0,
        "fees": 0.0,
        "peak_equity": 100_000.0,
        "positions": [
            {
                "instrument": "SBIN",
                "quantity": 1.5,
                "average_price": 1_000.0,
                "realized_pnl": 0.0,
            }
        ],
        "reserved_margin": {},
    }

    with pytest.raises(ValueError, match="invalid portfolio checkpoint"):
        portfolio.restore_state(state)
    assert portfolio.snapshot().positions == ()


def test_restore_state_accepts_integer_position_quantity():
    portfolio = Portfolio(100_000)
    state = {
        "cash": 98_000.0,
        "realized_pnl": 0.0,
        "fees": 0.0,
        "peak_equity": 100_000.0,
        "positions": [
            {
                "instrument": "SBIN",
                "quantity": 2,
                "average_price": 1_000.0,
                "realized_pnl": 0.0,
            }
        ],
        "reserved_margin": {},
    }

    portfolio.restore_state(state)
    assert portfolio.snapshot().positions[0].quantity == 2


def test_restore_state_rejects_duplicate_instrument_positions():
    portfolio = Portfolio(100_000)
    state = {
        "cash": 98_000.0,
        "realized_pnl": 0.0,
        "fees": 0.0,
        "peak_equity": 100_000.0,
        "positions": [
            {"instrument": "SBIN", "quantity": 1, "average_price": 1_000.0, "realized_pnl": 0.0},
            {"instrument": "SBIN", "quantity": 2, "average_price": 1_100.0, "realized_pnl": 0.0},
        ],
        "reserved_margin": {},
    }

    with pytest.raises(ValueError, match="invalid portfolio checkpoint"):
        portfolio.restore_state(state)
    assert portfolio.snapshot().positions == ()


def test_restore_state_rejects_boolean_numeric_checkpoint_fields():
    portfolio = Portfolio(100_000)
    state = {
        "cash": True,
        "realized_pnl": 0.0,
        "fees": 0.0,
        "peak_equity": 100_000.0,
        "positions": [],
        "reserved_margin": {},
    }

    with pytest.raises(ValueError, match="invalid portfolio checkpoint"):
        portfolio.restore_state(state)
    assert portfolio.snapshot().positions == ()


def test_restore_state_rejects_non_string_position_instrument():
    portfolio = Portfolio(100_000)
    state = {
        "cash": 100_000.0,
        "realized_pnl": 0.0,
        "fees": 0.0,
        "peak_equity": 100_000.0,
        "positions": [
            {"instrument": 123, "quantity": 1, "average_price": 1_000.0, "realized_pnl": 0.0},
        ],
        "reserved_margin": {},
    }

    with pytest.raises(ValueError, match="invalid portfolio checkpoint"):
        portfolio.restore_state(state)
    assert portfolio.snapshot().positions == ()
