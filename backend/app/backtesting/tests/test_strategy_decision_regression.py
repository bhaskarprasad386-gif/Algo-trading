import pytest

from app.backtesting.execution import ExecutionSide, SimOrder
from app.backtesting.strategy import StrategyDecision, validate_decision


def _order(order_id: str) -> SimOrder:
    return SimOrder(order_id, "X", ExecutionSide.BUY, 1)


def test_validate_decision_rejects_duplicate_order_ids():
    decision = StrategyDecision("BUY", (_order("same"), _order("same")))
    with pytest.raises(ValueError, match="duplicate order_id"):
        validate_decision(decision)


def test_validate_decision_rejects_non_string_action():
    decision = StrategyDecision(123, ())
    with pytest.raises(ValueError, match="action is required"):
        validate_decision(decision)


def test_validate_decision_rejects_non_mapping_metadata():
    decision = StrategyDecision("BUY", (), metadata=123)
    with pytest.raises(ValueError, match="metadata must be a mapping"):
        validate_decision(decision)
