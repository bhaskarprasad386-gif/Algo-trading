from __future__ import annotations

import pytest

from app.backtesting.provider_retry import build_provider_retry_policy


def test_angelone_profile_enforces_safe_request_spacing() -> None:
    policy = build_provider_retry_policy("angelone")

    assert policy.min_interval_seconds == pytest.approx(1 / 3)
    assert policy.base_delay_seconds == 1.0
    assert policy.max_delay_seconds == 30.0


def test_provider_name_is_case_insensitive() -> None:
    lower = build_provider_retry_policy("angelone")
    upper = build_provider_retry_policy("AngelOne")

    assert upper.min_interval_seconds == lower.min_interval_seconds
    assert upper.base_delay_seconds == lower.base_delay_seconds


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported historical provider"):
        build_provider_retry_policy("unknown-provider")
