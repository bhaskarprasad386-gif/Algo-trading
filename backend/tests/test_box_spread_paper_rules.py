import pytest

from app.scanner.box_spread_paper_rules import (
    validate_rule_config, entry_matches, exit_matches, compare,
)


def base_config():
    return {
        "entry": {
            "symbols": ["NIFTY", "SENSEX"], "direction": "LONG",
            "instrument_class": "INDEX", "expiry_type": "WEEKLY",
            "metric": "executable_edge", "operator": ">=", "threshold": 20,
            "lots": 5,
        },
        "exit": {
            "metric": "executable_edge", "operator": "<=", "threshold": 1,
            "hold_to_expiry": False,
        },
        "monitor": {"interval_seconds": 20},
    }


def test_custom_rule_accepts_entry_20_and_exit_at_1_with_20_second_monitor():
    config = validate_rule_config(base_config())
    assert config["entry"]["threshold"] == 20
    assert config["entry"]["lots"] == 5
    assert config["exit"]["threshold"] == 1
    assert config["monitor"]["interval_seconds"] == 20


def test_custom_rule_entry_filters_symbol_direction_and_edge():
    config = validate_rule_config(base_config())
    row = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry_type": "WEEKLY",
        "direction": "LONG", "executable_edge": 20.25,
    }
    assert entry_matches(row, config) == (True, "entry_conditions_met")
    row["executable_edge"] = 19.99
    assert entry_matches(row, config) == (False, "entry_threshold_not_met")
    row["symbol"] = "BANKNIFTY"
    assert entry_matches(row, config) == (False, "symbol_not_selected")


@pytest.mark.parametrize(("edge", "expected"), [(1.0, True), (0.0, True), (1.01, False), (-0.2, True)])
def test_custom_exit_edge_threshold(edge, expected):
    config = validate_rule_config(base_config())
    matched, reason, observed = exit_matches({"executable_edge": edge}, config)
    assert matched is expected
    assert observed == edge
    assert reason == ("exit_threshold_met" if expected else "exit_threshold_not_met")


def test_custom_rule_hold_to_expiry_can_be_selected_without_exit_threshold():
    raw = base_config()
    raw["exit"] = {"hold_to_expiry": True}
    config = validate_rule_config(raw)
    assert config["exit"]["hold_to_expiry"] is True


@pytest.mark.parametrize("interval", [0, 4, 3601, -20])
def test_custom_rule_rejects_unsafe_monitor_interval(interval):
    raw = base_config()
    raw["monitor"]["interval_seconds"] = interval
    with pytest.raises(ValueError):
        validate_rule_config(raw)


@pytest.mark.parametrize("metric", ["ltp", "spot_price", "__dict__"])
def test_custom_rule_rejects_unsupported_metrics(metric):
    raw = base_config()
    raw["entry"]["metric"] = metric
    with pytest.raises(ValueError):
        validate_rule_config(raw)


def test_compare_supports_inclusive_and_strict_thresholds():
    assert compare(1.0, "<=", 1.0)
    assert not compare(1.0, "<", 1.0)
    assert compare(20.0, ">=", 20.0)
    assert not compare(20.0, ">", 20.0)

def test_exit_order_type_defaults_to_limit_and_market_is_supported():
    config = validate_rule_config(base_config())
    assert config["exit"]["order_type"] == "LIMIT"

    raw = base_config()
    raw["exit"]["order_type"] = "market"
    config = validate_rule_config(raw)
    assert config["exit"]["order_type"] == "MARKET"


@pytest.mark.parametrize("order_type", ["STOP", "IOC", ""])
def test_exit_order_type_rejects_unsupported_values(order_type):
    raw = base_config()
    raw["exit"]["order_type"] = order_type
    with pytest.raises(ValueError, match="exit.order_type"):
        validate_rule_config(raw)

