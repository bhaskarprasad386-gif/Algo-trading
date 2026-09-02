import pytest

from app.execution.retry import RetryExhaustedError, RetryPolicy, execute_with_retry


def test_retry_succeeds_before_exhaustion():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return "ok"

    assert execute_with_retry(operation, RetryPolicy(max_attempts=2)) == "ok"
    assert calls == 2


def test_fallback_runs_after_primary_attempts_fail():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise RuntimeError("primary down")

    assert execute_with_retry(operation, RetryPolicy(max_attempts=2), lambda: "fallback") == "fallback"
    assert calls == 2


def test_exhausted_without_fallback_raises():
    with pytest.raises(RetryExhaustedError):
        execute_with_retry(lambda: (_ for _ in ()).throw(RuntimeError("down")))


def test_retry_policy_rejects_zero_attempts():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
