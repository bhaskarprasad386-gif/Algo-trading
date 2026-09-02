from app.execution.confirmation import ConfirmationGateway
from app.execution.dual_engine import DualExecutionEngine, ExecutionMode, Fill
from app.execution.idempotency import IdempotencyGuard
from app.execution.safety import SafetyController, SafetyLimits


def test_live_entry_requires_confirmation_and_allows_once() -> None:
    calls: list[tuple[ExecutionMode, float]] = []

    def executor(mode: ExecutionMode, price: float, quantity: float) -> Fill:
        calls.append((mode, quantity))
        return Fill(price, quantity)

    engine = DualExecutionEngine(
        executor,
        confirmation=ConfirmationGateway(ttl_seconds=30),
        idempotency=IdempotencyGuard(),
        safety=SafetyController(SafetyLimits(daily_loss_limit=100)),
    )

    engine.create_live_confirmation("req-1")
    fill = engine.enter_live(100.0, 7, "req-1")

    assert fill.price == 100.0
    assert fill.quantity == 7
    assert calls == [(ExecutionMode.LIVE, 7)]

    try:
        engine.enter_live(100.0, 7, "req-1")
    except RuntimeError as exc:
        assert "confirmation" in str(exc)
    else:
        raise AssertionError("duplicate request must not execute")


def test_live_entry_is_blocked_by_safety_before_execution() -> None:
    calls: list[tuple[ExecutionMode, float]] = []

    def executor(mode: ExecutionMode, price: float, quantity: float) -> Fill:
        calls.append((mode, quantity))
        return Fill(price, quantity)

    safety = SafetyController(SafetyLimits(daily_loss_limit=100))
    safety.activate_kill_switch()
    engine = DualExecutionEngine(executor, safety=safety)
    engine.create_live_confirmation("blocked")

    try:
        engine.enter_live(100.0, 7, "blocked")
    except RuntimeError as exc:
        assert "safety" in str(exc)
    else:
        raise AssertionError("killed engine must not execute")

    assert calls == []


def test_paper_entry_forwards_requested_quantity() -> None:
    calls: list[tuple[ExecutionMode, float]] = []

    def executor(mode: ExecutionMode, price: float, quantity: float) -> Fill:
        calls.append((mode, quantity))
        return Fill(price, quantity)

    engine = DualExecutionEngine(executor)
    fill = engine.enter(100.0, 13)

    assert fill.quantity == 13
    assert calls == [(ExecutionMode.PAPER, 13)]
