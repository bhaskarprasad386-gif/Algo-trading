from backend.app.execution.confirmation import ConfirmationGateway
from backend.app.execution.dual_engine import DualExecutionEngine, ExecutionMode, Fill
from backend.app.execution.idempotency import IdempotencyGuard
from backend.app.execution.safety import SafetyController, SafetyLimits


def test_live_entry_requires_confirmation_and_allows_once() -> None:
    calls: list[ExecutionMode] = []

    def executor(mode: ExecutionMode, price: float) -> Fill:
        calls.append(mode)
        return Fill(price, 1)

    engine = DualExecutionEngine(
        executor,
        confirmation=ConfirmationGateway(ttl_seconds=30),
        idempotency=IdempotencyGuard(),
        safety=SafetyController(SafetyLimits(daily_loss_limit=100)),
    )

    engine.create_live_confirmation("req-1")
    fill = engine.enter_live(100.0, 1, "req-1")

    assert fill.price == 100.0
    assert calls == [ExecutionMode.LIVE]

    try:
        engine.enter_live(100.0, 1, "req-1")
    except RuntimeError as exc:
        assert "confirmation" in str(exc)
    else:
        raise AssertionError("duplicate request must not execute")


def test_live_entry_is_blocked_by_safety_before_execution() -> None:
    calls: list[ExecutionMode] = []

    def executor(mode: ExecutionMode, price: float) -> Fill:
        calls.append(mode)
        return Fill(price, 1)

    safety = SafetyController(SafetyLimits(daily_loss_limit=100))
    safety.activate_kill_switch()
    engine = DualExecutionEngine(executor, safety=safety)
    engine.create_live_confirmation("blocked")

    try:
        engine.enter_live(100.0, 1, "blocked")
    except RuntimeError as exc:
        assert "safety" in str(exc)
    else:
        raise AssertionError("killed engine must not execute")

    assert calls == []
