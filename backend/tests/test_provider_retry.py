import pytest

from app.backtesting.provider_retry import ProviderRetryPolicy


class StatusError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


def test_before_attempt_enforces_minimum_interval():
    now = [0.0]
    sleeps = []

    def clock():
        return now[0]

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    policy = ProviderRetryPolicy(min_interval_seconds=1.0, clock=clock, sleeper=sleep)
    policy.before_attempt()
    now[0] += 0.25
    policy.before_attempt()

    assert sleeps == [0.75]


def test_delay_is_exponential_and_capped_without_jitter():
    policy = ProviderRetryPolicy(base_delay_seconds=2.0, max_delay_seconds=5.0, jitter_ratio=0)
    assert policy.delay(1) == 2.0
    assert policy.delay(2) == 4.0
    assert policy.delay(3) == 5.0
    assert policy.delay(5) == 5.0


def test_jitter_stays_within_configured_bounds():
    low = ProviderRetryPolicy(base_delay_seconds=8.0, max_delay_seconds=8.0, jitter_ratio=0.25, random_fn=lambda: 0.0)
    high = ProviderRetryPolicy(base_delay_seconds=8.0, max_delay_seconds=8.0, jitter_ratio=0.25, random_fn=lambda: 1.0)
    assert low.delay(1) == 6.0
    assert high.delay(1) == 10.0


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), StatusError(429), StatusError(500), StatusError(503)])
def test_transient_errors_are_retryable(error):
    assert ProviderRetryPolicy.is_transient(error)


@pytest.mark.parametrize("error", [ValueError("bad data"), StatusError(400), StatusError(401), StatusError(404)])
def test_permanent_errors_are_not_retryable(error):
    assert not ProviderRetryPolicy.is_transient(error)


def test_invalid_policy_values_are_rejected():
    with pytest.raises(ValueError):
        ProviderRetryPolicy(min_interval_seconds=-1)
    with pytest.raises(ValueError):
        ProviderRetryPolicy(base_delay_seconds=5, max_delay_seconds=4)
    with pytest.raises(ValueError):
        ProviderRetryPolicy(jitter_ratio=1.1)


def test_invalid_attempt_is_rejected():
    with pytest.raises(ValueError):
        ProviderRetryPolicy().delay(0)
