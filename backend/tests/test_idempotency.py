import pytest

from app.execution.idempotency import ExecutionRequest, IdempotencyGuard


def test_same_execution_key_is_accepted_only_once():
    guard = IdempotencyGuard()
    request = ExecutionRequest("order-123")

    assert guard.accept(request) is True
    assert guard.accept(request) is False


def test_different_execution_keys_are_independent():
    guard = IdempotencyGuard()

    assert guard.accept(ExecutionRequest("order-1")) is True
    assert guard.accept(ExecutionRequest("order-2")) is True


def test_empty_execution_key_is_rejected():
    with pytest.raises(ValueError):
        ExecutionRequest("   ")
