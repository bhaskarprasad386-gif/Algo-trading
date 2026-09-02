import pytest

from app.execution.confirmation import ConfirmationGateway
from app.execution.dual_engine import ExecutionConfig, ExecutionMode, Fill, DualExecutionEngine


def test_same_signal_keeps_paper_and_live_state_separate_and_adjusts_from_actual_fills():
    fills = {
        ExecutionMode.PAPER: Fill(price=101, quantity=2),
        ExecutionMode.LIVE: Fill(price=102, quantity=2),
    }

    def executor(mode, requested_price, quantity):
        return fills[mode]

    confirmation = ConfirmationGateway(ttl_seconds=30)
    engine = DualExecutionEngine(
        executor,
        ExecutionConfig(stop_loss_pct=0.02, target_pct=0.04),
        confirmation=confirmation,
    )

    paper_fill = engine.enter(price=100, quantity=2)
    assert paper_fill.price == 101
    assert engine.paper.entry_price == 101
    assert engine.paper.stop_loss == pytest.approx(98.98)
    assert engine.paper.target == pytest.approx(105.04)
    assert engine.paper.quantity == 2

    request_id = "dual-state-live-1"
    engine.create_live_confirmation(request_id)
    live_fill = engine.enter_live(price=100, quantity=2, request_id=request_id)

    assert live_fill.price == 102
    assert engine.live.entry_price == 102
    assert engine.paper.entry_price != engine.live.entry_price
    assert engine.live.stop_loss == pytest.approx(99.96)
    assert engine.live.target == pytest.approx(106.08)
    assert engine.live.quantity == 2


def test_execution_config_rejects_negative_adjustments():
    with pytest.raises(ValueError):
        ExecutionConfig(stop_loss_pct=-0.01)


def test_fill_rejects_non_positive_values():
    with pytest.raises(ValueError):
        Fill(price=0, quantity=1)
