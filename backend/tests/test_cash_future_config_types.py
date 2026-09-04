import pytest

from app.scanner.cash_future import CashFutureConfig


@pytest.mark.parametrize("field", ["enabled", "require_two_sided_quotes"])
@pytest.mark.parametrize("value", [1, 0, 1.0, 0.0, "true", None])
def test_boolean_config_fields_require_real_booleans(field, value):
    with pytest.raises(ValueError, match=rf"{field} must be a boolean"):
        CashFutureConfig(**{field: value})


def test_boolean_config_fields_accept_true_and_false():
    config = CashFutureConfig(enabled=False, require_two_sided_quotes=False)
    assert config.enabled is False
    assert config.require_two_sided_quotes is False
