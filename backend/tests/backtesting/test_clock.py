from app.backtesting.clock import BacktestClock, ClockProtocol


def test_backtest_clock_is_deterministic_and_protocol_compatible():
    clock = BacktestClock()
    assert isinstance(clock, ClockProtocol)
    assert clock.now_ns == 0
    assert clock.advance_to(1_000_000) == 1_000_000
    assert clock.now_ns == 1_000_000
    assert clock.advance_to(1_000_000) == 1_000_000


def test_backtest_clock_rejects_backward_time():
    clock = BacktestClock(100)
    try:
        clock.advance_to(99)
    except ValueError as exc:
        assert "backwards" in str(exc)
    else:
        raise AssertionError("expected backward clock movement to fail")


def test_backtest_clock_rejects_invalid_timestamps():
    clock = BacktestClock()
    for value in (-1, True, 1.5):
        try:
            clock.advance_to(value)
        except ValueError:
            pass
        else:
            raise AssertionError("expected invalid timestamp to fail")
