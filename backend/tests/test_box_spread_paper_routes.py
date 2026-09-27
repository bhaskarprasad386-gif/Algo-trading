from app.execution.box_spread_paper_routes import _entry_cashflow, _exit_cashflow


class P:
    direction = "LONG"


def _entry(direction):
    return type("E", (), {
        "direction": direction,
        "low_call_price": 11.0, "low_put_price": 2.0,
        "high_call_price": 9.0, "high_put_price": 3.0,
    })()


def _exit(low_call, low_put, high_call, high_put):
    return type("X", (), {
        "low_call_price": low_call, "low_put_price": low_put,
        "high_call_price": high_call, "high_put_price": high_put,
    })()


def test_box_spread_paper_uses_executable_entry_cashflows():
    assert _entry_cashflow(_entry("LONG")) == -1.0
    assert _entry_cashflow(_entry("SHORT")) == 1.0


def test_box_spread_paper_long_close_cashflow_and_pnl_sign():
    entry = _entry("LONG")
    position = type("P", (), {"direction": "LONG"})()
    close = _exit(12, 1, 10, 2)
    assert _entry_cashflow(entry) + _exit_cashflow(position, close) == 1.0


def test_box_spread_paper_short_close_cashflow_and_pnl_sign():
    entry = _entry("SHORT")
    position = type("P", (), {"direction": "SHORT"})()
    close = _exit(12, 1, 10, 2)
    assert _entry_cashflow(entry) + _exit_cashflow(position, close) == -1.0
