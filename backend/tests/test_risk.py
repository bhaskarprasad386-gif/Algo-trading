from app.execution.risk import PreTradeLimits, PreTradeOrder, check_pre_trade


def base_order(**overrides):
    values = dict(
        price=100,
        quantity=10,
        stop_loss=98,
        required_margin=500,
        available_margin=2_000,
        lower_circuit=90,
        upper_circuit=110,
    )
    values.update(overrides)
    return PreTradeOrder(**values)


def test_pre_trade_approves_order_with_margin_risk_and_circuit_headroom():
    result = check_pre_trade(base_order(), PreTradeLimits(max_risk_amount=50, min_available_margin=1_000))
    assert result.approved is True
    assert result.reasons == ()


def test_pre_trade_rejects_insufficient_margin_and_risk():
    result = check_pre_trade(
        base_order(required_margin=2_100, available_margin=1_000, quantity=30),
        PreTradeLimits(max_risk_amount=50),
    )
    assert result.approved is False
    assert "insufficient_margin" in result.reasons
    assert "max_risk_exceeded" in result.reasons


def test_pre_trade_rejects_circuit_breach_and_invalid_order():
    result = check_pre_trade(
        base_order(price=120, quantity=0, stop_loss=-1),
        PreTradeLimits(max_risk_amount=50),
    )
    assert result.approved is False
    assert "invalid_order_size" in result.reasons
    assert "invalid_stop_loss" in result.reasons
    assert "above_upper_circuit" in result.reasons


def test_limits_reject_negative_values():
    import pytest

    with pytest.raises(ValueError):
        PreTradeLimits(max_risk_amount=-1)
